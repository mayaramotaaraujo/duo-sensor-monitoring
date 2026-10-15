# ============================================================
#   DUO · OBSERVABILIDADE
#
#   Guarda uma linha para cada chamada que chega na API e
#   calcula os indicadores do contrato de métricas.
#
#   A ideia é a mesma da tabela "registro" da Sprint 4, que
#   guardava quem fez o quê. Aqui a pergunta muda: quem chamou,
#   de qual tela, quando, quanto demorou e se o dado que saiu
#   estava fresco e completo.
# ============================================================

import json
import os
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pandas as pd

PASTA_DADOS = Path(__file__).resolve().parent.parent / 'dados'
CAMINHO_OBS = Path(os.environ.get('DUO_BANCO_OBSERVABILIDADE',
                                  PASTA_DADOS / 'observabilidade.db'))
CAMINHO_CONTRATO = Path(__file__).resolve().parent / 'contrato_metricas.json'

ROTA_PAINEL = '/v1/observabilidade'
_trava = threading.Lock()

COLUNAS = ['request_id', 'momento', 'session_id', 'feature',
           'headers_completos', 'metodo', 'rota', 'caminho', 'tag',
           'status', 'latencia_ms', 'idade_dado_s', 'limite_freshness_s',
           'dentro_freshness', 'fase', 'origem', 'completude_pct',
           'severidade', 'quantidade_leituras', 'cliente', 'erro']


def carregar_contrato():
    with open(CAMINHO_CONTRATO, encoding='utf-8') as f:
        return json.load(f)


def abrir():
    return sqlite3.connect(CAMINHO_OBS, timeout=20, check_same_thread=False)


def criar_estrutura():
    CAMINHO_OBS.parent.mkdir(parents=True, exist_ok=True)
    con = abrir()
    con.execute("""
        CREATE TABLE IF NOT EXISTS chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT, momento TEXT NOT NULL,
            session_id TEXT, feature TEXT, headers_completos INTEGER,
            metodo TEXT, rota TEXT, caminho TEXT, tag TEXT,
            status INTEGER, latencia_ms REAL,
            idade_dado_s REAL, limite_freshness_s REAL,
            dentro_freshness INTEGER, fase TEXT, origem TEXT,
            completude_pct REAL, severidade TEXT,
            quantidade_leituras INTEGER, cliente TEXT, erro TEXT)""")
    con.execute('CREATE INDEX IF NOT EXISTS idx_momento ON chamadas(momento)')
    con.commit()
    con.close()


def gravar(registro):
    valores = [registro.get(c) for c in COLUNAS]
    with _trava:
        con = abrir()
        con.execute(f"INSERT INTO chamadas ({', '.join(COLUNAS)}) "
                    f"VALUES ({', '.join('?' * len(COLUNAS))})", valores)
        con.commit()
        con.close()


def limite_freshness(contrato, origem, fase):
    por_fonte = contrato['freshness_limites_s'].get(origem)
    if not por_fonte:
        return None
    return por_fonte.get(fase, por_fonte.get('girando'))


def reavaliar_freshness(tabela, contrato):
    """Quando o contrato muda, as chamadas antigas são julgadas pelo
       limite novo. O limite que valia na hora fica guardado ao lado,
       para ninguém perder a história."""
    if tabela.empty:
        return tabela
    tabela = tabela.copy()
    tabela['limite_na_chamada_s'] = tabela['limite_freshness_s']
    tem_idade = tabela['idade_dado_s'].notna()
    novos = [limite_freshness(contrato, o, f) if t else None
             for o, f, t in zip(tabela['origem'], tabela['fase'], tem_idade)]
    tabela['limite_freshness_s'] = novos
    dentro = (tabela['idade_dado_s'] <= tabela['limite_freshness_s'].astype(float))
    tabela['dentro_freshness'] = dentro.astype(int).where(tem_idade)
    return tabela


def ler_chamadas(minutos=None, feature=None, session_id=None,
                 incluir_painel=False, limite=None, de=None, ate=None):
    con = abrir()
    sql = 'SELECT * FROM chamadas WHERE 1=1'
    parametros = []
    if minutos:
        sql += " AND momento >= strftime('%Y-%m-%dT%H:%M:%f', 'now', ?)"
        parametros.append(f'-{float(minutos)} minutes')
    # de e ate chegam em UTC, no mesmo formato gravado em momento
    if de:
        sql += ' AND momento >= ?'
        parametros.append(de.strftime('%Y-%m-%dT%H:%M:%S.000'))
    if ate:
        sql += ' AND momento <= ?'
        parametros.append(ate.strftime('%Y-%m-%dT%H:%M:%S.999'))
    if feature:
        sql += ' AND feature = ?'
        parametros.append(feature)
    if session_id:
        sql += ' AND session_id = ?'
        parametros.append(session_id)
    if not incluir_painel:
        sql += ' AND rota NOT LIKE ?'
        parametros.append(ROTA_PAINEL + '%')
    sql += ' ORDER BY id DESC'
    if limite:
        sql += f' LIMIT {int(limite)}'
    tabela = pd.read_sql_query(sql, con, params=parametros)
    con.close()
    return tabela


# ══════════════════════════════════════════════════════════════
#  OS INDICADORES
# ══════════════════════════════════════════════════════════════

def _situacao(valor, regra):
    """Compara o valor com a regra do contrato. Devolve ok,
       atencao, estouro ou sem_dado."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return 'sem_dado'
    atencao, estouro = regra.get('atencao'), regra.get('estouro')
    direcao = regra.get('direcao')
    base = regra.get('baseline')

    if direcao == 'desvio':
        if base is None:
            return 'sem_baseline'
        if regra.get('unidade') == 'chamadas/min':
            if base <= 0:
                return 'sem_baseline'
            vezes = valor / base
            return ('estouro' if vezes >= 10 else
                    'atencao' if vezes >= 3 else 'ok')
        distancia = abs(valor - base)
        return ('estouro' if distancia >= 30 else
                'atencao' if distancia >= 15 else 'ok')

    if not isinstance(atencao, (int, float)) or \
            not isinstance(estouro, (int, float)):
        return 'sem_limiar'
    if direcao == 'maior_pior':
        return ('estouro' if valor >= estouro else
                'atencao' if valor >= atencao else 'ok')
    # menor_pior: abaixo da atenção já pede cuidado
    return ('estouro' if valor < estouro else
            'atencao' if valor < atencao else 'ok')


def _p95(serie):
    serie = serie.dropna()
    return round(float(np.percentile(serie, 95)), 2) if len(serie) else None


def _pct(parte, total):
    return round(100.0 * parte / total, 2) if total else None


def calcular_indicadores(tabela, contrato):
    regras = contrato['indicadores']
    total = len(tabela)
    ind = {}

    def montar(chave, valor, detalhe=None):
        regra = regras[chave]
        ind[chave] = {
            'nome': regra['nome'], 'valor': valor,
            'unidade': regra['unidade'], 'baseline': regra.get('baseline'),
            'atencao': regra.get('atencao'), 'estouro': regra.get('estouro'),
            'situacao': _situacao(valor, regra),
            'acao': None, 'detalhe': detalhe or {},
        }
        s = ind[chave]['situacao']
        if s == 'atencao':
            ind[chave]['acao'] = regra['acao_atencao']
        elif s == 'estouro':
            ind[chave]['acao'] = regra['acao_estouro']

    if total == 0:
        for chave in regras:
            montar(chave, None)
        return {'total_chamadas': 0, 'indicadores': ind}

    # 1. latência
    por_rota = {r: _p95(g['latencia_ms']) for r, g in tabela.groupby('rota')}
    montar('latencia_p95_ms', _p95(tabela['latencia_ms']),
           {'por_rota': por_rota,
            'media_ms': round(float(tabela['latencia_ms'].mean()), 2)})

    # 2 e 3. erros
    n5 = int((tabela['status'] >= 500).sum())
    n4 = int(((tabela['status'] >= 400) & (tabela['status'] < 500)).sum())
    montar('taxa_erro_servidor_pct', _pct(n5, total), {'chamadas_5xx': n5})
    montar('taxa_erro_cliente_pct', _pct(n4, total), {'chamadas_4xx': n4})

    # 4 e 5. freshness, só nas leituras atuais que trouxeram dado
    atuais = tabela[tabela['rota'].str.contains('leitura-atual', na=False)
                    & tabela['idade_dado_s'].notna()]
    # cada fonte tem o seu ritmo, então o percentil 95 é comparado
    # com o limite da própria fonte e fase, nunca misturado
    grupos = {}
    pior_razao = None
    for (origem, fase), g in atuais.groupby(['origem', 'fase']):
        p95_g = _p95(g['idade_dado_s'])
        lim_g = float(g['limite_freshness_s'].iloc[0])
        razao_g = p95_g / lim_g if lim_g else None
        grupos[f'{origem} · {fase}'] = {
            'p95_s': p95_g, 'limite_s': lim_g, 'chamadas': int(len(g)),
            'atrasadas': int((g['idade_dado_s'] > lim_g).sum())}
        if razao_g is not None:
            pior_razao = razao_g if pior_razao is None else max(pior_razao,
                                                                razao_g)
    ind_fresh = {
        'nome': regras['freshness_p95_s']['nome'],
        'valor': round(pior_razao, 2) if pior_razao is not None else None,
        'unidade': 'x o limite',
        'baseline': regras['freshness_p95_s'].get('baseline'),
        'atencao': 1, 'estouro': 5, 'acao': None,
        'detalhe': {'por_fonte_e_fase': grupos,
                    'p95_geral_s': _p95(atuais['idade_dado_s'])},
    }
    if pior_razao is None:
        ind_fresh['situacao'] = 'sem_dado'
    else:
        ind_fresh['situacao'] = ('estouro' if pior_razao > 5 else
                                 'atencao' if pior_razao > 1 else 'ok')
    if ind_fresh['situacao'] == 'atencao':
        ind_fresh['acao'] = regras['freshness_p95_s']['acao_atencao']
    elif ind_fresh['situacao'] == 'estouro':
        ind_fresh['acao'] = regras['freshness_p95_s']['acao_estouro']
    ind['freshness_p95_s'] = ind_fresh

    dentro = atuais['dentro_freshness'].sum() if len(atuais) else 0
    montar('completude_freshness_pct', _pct(dentro, len(atuais)),
           {'leituras_atuais': int(len(atuais)),
            'por_fonte_e_fase': {f'{o} · {f}': _pct(g['dentro_freshness'].sum(),
                                                     len(g))
                                 for (o, f), g in atuais.groupby(['origem',
                                                                   'fase'])}})

    # 6. completude dos campos
    com_dado = tabela[tabela['completude_pct'].notna()]
    montar('completude_campos_pct',
           round(float(com_dado['completude_pct'].mean()), 2)
           if len(com_dado) else None,
           {'por_origem': {o: round(float(g['completude_pct'].mean()), 2)
                           for o, g in com_dado.groupby('origem')}})

    # 7. rastreabilidade
    montar('rastreabilidade_pct',
           _pct(int(tabela['headers_completos'].sum()), total),
           {'sem_contexto': int((tabela['headers_completos'] == 0).sum()),
            'por_cliente': {c: _pct(int(g['headers_completos'].sum()), len(g))
                            for c, g in tabela.groupby(
                                tabela['cliente'].fillna('desconhecido'))}})

    # 8. volume
    momentos = pd.to_datetime(tabela['momento'])
    minutos = max((momentos.max() - momentos.min()).total_seconds() / 60, 1.0)
    montar('volume_por_minuto', round(total / minutos, 2),
           {'minutos_observados': round(minutos, 1),
            'por_feature': {f: round(len(g) / minutos, 2)
                            for f, g in tabela.groupby(
                                tabela['feature'].fillna('sem feature'))},
            'sessoes': int(tabela['session_id'].nunique())})

    # 9. severidade
    sev = atuais['severidade'].value_counts()
    montar('taxa_severidade_vermelha_pct',
           _pct(int(sev.get('vermelho', 0)), len(atuais)),
           {'distribuicao': {k: int(v) for k, v in sev.items()}})

    return {'total_chamadas': total, 'indicadores': ind}
