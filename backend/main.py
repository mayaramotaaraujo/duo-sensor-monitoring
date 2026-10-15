# ============================================================
#   DUO · SENSOR MONITORING API
#
#   Checkpoint integrado de Governança em IA e Front-end.
#   Expõe o provider de Sensores do DUO e registra cada chamada.
#
#   Para rodar, de dentro da pasta do projeto:
#       uvicorn backend.main:app --reload --port 8000
# ============================================================

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend import observabilidade as obs
from backend.provider_sensores import ProviderSensores

VELOCIDADE = float(os.environ.get('DUO_VELOCIDADE', '1'))
INICIO_S = float(os.environ.get('DUO_INICIO_S', '0'))
EXIGIR_CONTEXTO = os.environ.get('DUO_EXIGIR_CONTEXTO', '0') == '1'

app = FastAPI(
    title='DUO · Sensor Monitoring API',
    version='1.0',
    description='Leitura atual e histórico dos sensores do DUO, com '
                'registro de observabilidade em cada chamada.',
)

provider = ProviderSensores(velocidade=VELOCIDADE, inicio_s=INICIO_S)
contrato = obs.carregar_contrato()
obs.criar_estrutura()


def agora_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]


# ══════════════════════════════════════════════════════════════
#  O REGISTRO DE CADA CHAMADA
#
#  Fica num middleware para nenhuma rota escapar. As rotas só
#  deixam em request.state.obs o que sabem sobre o dado que
#  entregaram (idade, fase, completude, cor).
# ══════════════════════════════════════════════════════════════

@app.middleware('http')
async def registrar_chamada(request: Request, call_next):
    caminho = request.url.path
    if not caminho.startswith('/v1/'):
        return await call_next(request)

    inicio = time.perf_counter()
    momento = agora_utc()
    request_id = uuid.uuid4().hex[:16]
    session_id = request.headers.get('X-Session-Id')
    feature = request.headers.get('X-Feature')
    completos = int(bool(session_id) and bool(feature))
    request.state.obs = {}
    erro = None

    if EXIGIR_CONTEXTO and not completos:
        resposta = JSONResponse(status_code=400, content={
            'erro': 'chamada sem contexto',
            'detalhe': 'envie os headers X-Session-Id e X-Feature'})
        erro = 'recusada por falta de contexto'
    else:
        try:
            resposta = await call_next(request)
        except Exception as e:  # o que escapar das rotas vira 500
            resposta = JSONResponse(status_code=500, content={
                'erro': 'falha interna', 'request_id': request_id})
            erro = f'{type(e).__name__}: {str(e)[:200]}'

    latencia = (time.perf_counter() - inicio) * 1000
    rota = getattr(request.scope.get('route'), 'path', caminho)
    extra = getattr(request.state, 'obs', {}) or {}

    obs.gravar({
        'request_id': request_id, 'momento': momento,
        'session_id': session_id, 'feature': feature,
        'headers_completos': completos,
        'metodo': request.method, 'rota': rota, 'caminho': caminho,
        'tag': request.path_params.get('tag') if hasattr(
            request, 'path_params') else None,
        'status': resposta.status_code, 'latencia_ms': round(latencia, 3),
        'cliente': request.headers.get('User-Agent'),
        'erro': erro or extra.get('erro'),
        **{k: extra.get(k) for k in ('idade_dado_s', 'limite_freshness_s',
                                      'dentro_freshness', 'fase', 'origem',
                                      'completude_pct', 'severidade',
                                      'quantidade_leituras')},
    })

    resposta.headers['X-Request-Id'] = request_id
    resposta.headers['X-Latencia-ms'] = f'{latencia:.2f}'
    return resposta


def _confere_tag(request, tag):
    if not provider.existe(tag):
        request.state.obs = {'erro': f'motor {tag} não cadastrado'}
        raise HTTPException(status_code=404,
                            detail=f'O motor {tag} não está cadastrado.')


# ══════════════════════════════════════════════════════════════
#  AS ROTAS
# ══════════════════════════════════════════════════════════════

@app.get('/')
def inicio():
    return {'sistema': 'DUO · Sensor Monitoring API',
            'documentacao': '/docs',
            'rotas': ['/v1/sensores',
                      '/v1/sensores/{tag}/leitura-atual',
                      '/v1/sensores/{tag}/historico',
                      '/v1/observabilidade',
                      '/v1/observabilidade/contrato'],
            'headers_de_contexto': ['X-Session-Id', 'X-Feature'],
            'exige_contexto': EXIGIR_CONTEXTO}


@app.get('/v1/sensores')
def listar_sensores():
    return {'motores': provider.tags()}


@app.get('/v1/sensores/{tag}/leitura-atual')
def leitura_atual(request: Request, tag: str,
                  base: Literal['rigida', 'flexivel'] = 'rigida'):
    _confere_tag(request, tag)
    resposta = provider.leitura_atual(tag, base)

    if resposta['tem_leitura']:
        limite = obs.limite_freshness(contrato, resposta['origem'],
                                      resposta['fase'])
        dentro = (int(resposta['idade_dado_s'] <= limite)
                  if limite is not None else None)
        resposta['freshness'] = {
            'idade_s': resposta['idade_dado_s'], 'limite_s': limite,
            'situacao': ('em dia' if dentro else 'atrasada')
            if dentro is not None else 'sem limite definido'}
        request.state.obs = {
            'idade_dado_s': resposta['idade_dado_s'],
            'limite_freshness_s': limite, 'dentro_freshness': dentro,
            'fase': resposta['fase'], 'origem': resposta['origem'],
            'completude_pct': resposta['completude']['pct'],
            'severidade': resposta['severidade']['cor'],
            'quantidade_leituras': 1}
    else:
        request.state.obs = {'severidade': 'cinza',
                             'quantidade_leituras': 0}
    return resposta


@app.get('/v1/sensores/{tag}/historico')
def historico(request: Request, tag: str,
              minutos: float = Query(30, gt=0, le=24 * 60,
                                     description='janela ao vivo, em minutos'),
              limite: int = Query(1500, gt=0, le=10000),
              fonte: Literal['ao_vivo', 'gravado'] = 'ao_vivo',
              de: Optional[datetime] = None, ate: Optional[datetime] = None,
              base: Literal['rigida', 'flexivel'] = 'rigida'):
    _confere_tag(request, tag)
    if fonte == 'gravado':
        resposta = provider.historico_gravado(tag, de, ate, limite, base)
    else:
        resposta = provider.historico_ao_vivo(tag, minutos, limite, base)
    resposta['fonte'] = fonte
    request.state.obs = {
        'origem': resposta['origem'],
        'completude_pct': resposta['completude_media_pct'],
        'quantidade_leituras': resposta['quantidade']}
    return resposta


@app.get('/v1/observabilidade')
def observabilidade(
        limite: int = Query(300, gt=0, le=20000,
                            description='quantos registros devolver'),
        minutos: Optional[float] = Query(None, gt=0,
                                         description='só a janela recente'),
        feature: Optional[str] = None,
        session_id: Optional[str] = None,
        de: Optional[datetime] = Query(None, description='início, em UTC'),
        ate: Optional[datetime] = Query(None, description='fim, em UTC'),
        incluir_painel: bool = Query(
            False, description='contar também as chamadas a este endpoint')):
    tabela = obs.ler_chamadas(minutos, feature, session_id, incluir_painel,
                              de=de, ate=ate)
    tabela = obs.reavaliar_freshness(tabela, contrato)
    calculo = obs.calcular_indicadores(tabela, contrato)
    registros = tabela.head(limite).replace({float('nan'): None})
    return {
        'gerado_em': agora_utc() + 'Z',
        'filtros': {'minutos': minutos, 'feature': feature,
                    'session_id': session_id, 'de': de, 'ate': ate,
                    'incluir_painel': incluir_painel},
        **calculo,
        'registros': registros.to_dict(orient='records'),
    }


@app.get('/v1/observabilidade/contrato')
def ver_contrato():
    return contrato
