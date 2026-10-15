# ============================================================
#   DUO · PROVIDER DE SENSORES
#
#   É o mesmo provider que construímos na Sprint 4 (módulos de
#   coleta, base histórica e acompanhamento), separado do resto
#   do DUO para virar API.
#
#   Ficou de fora o que pesava e não fazia parte desta entrega:
#   os modelos treinados, a leitura da placa, a planta e o chat.
#   A severidade aqui usa só os olhares que vêm da norma.
# ============================================================

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PASTA_DADOS = Path(__file__).resolve().parent.parent / 'dados'
CAMINHO_BANCO = PASTA_DADOS / 'duo.db'
PASTA_TABELAS = PASTA_DADOS / 'tabelas'

# as grandezas que o sensor pode entregar, com a unidade
GRANDEZAS = {
    'velocidade_1': 'mm/s', 'aceleracao_1': 'g', 'pico_1': 'g',
    'temperatura_1': 'C',
    'velocidade_2': 'mm/s', 'aceleracao_2': 'g', 'pico_2': 'g',
    'temperatura_2': 'C',
    'tensao_v': 'V', 'corrente_a': 'A', 'rotacao_rpm': 'rpm',
}

# ══════════════════════════════════════════════════════════════
#  DE ONDE VEM A LEITURA DE CADA MOTOR
#
#  Não existe sensor ligado na bancada agora. Por isso a leitura
#  "ao vivo" é o histórico real reproduzido no ritmo em que ele
#  chegou na fábrica, inclusive com as pausas de quando o motor
#  estava parado. É o mesmo papel da FonteSimulador da Sprint 4.
#
#  campos_esperados: o que aquele sensor DEVERIA mandar. É contra
#  essa lista que a completude é medida.
# ══════════════════════════════════════════════════════════════

FONTES = {
    'BBA-001': {
        'origem': 'arquivo exportado',
        'descricao': 'histórico de 19/05/2026 exportado do sistema da '
                     'fábrica, dois sensores VIM32PL',
        'campos_esperados': ['velocidade_1', 'aceleracao_1', 'pico_1',
                             'temperatura_1', 'velocidade_2',
                             'aceleracao_2', 'pico_2', 'temperatura_2'],
    },
    'REF-001': {
        'origem': 'planilha processada',
        'descricao': 'conjunto de referência de 12 horas, motor que '
                     'piora com o tempo',
        'campos_esperados': ['velocidade_1', 'temperatura_1',
                             'corrente_a', 'rotacao_rpm', 'tensao_v'],
    },
}

# o que a fonte de agosto (endereço da fábrica) deveria mandar
CAMPOS_ESPERADOS_GRAVADO = {
    'endereço da fábrica': FONTES['BBA-001']['campos_esperados'],
    'arquivo exportado': FONTES['BBA-001']['campos_esperados'],
    'planilha processada': FONTES['REF-001']['campos_esperados'],
}

LIMIAR_PARADO = 0.30      # mm/s, tabela de fases da Sprint 4
LIMIAR_CARGA_ALTA = 5.5   # mm/s, usado para o fator de crista


def abrir_banco():
    """O banco do DUO é aberto só para leitura. A API nunca
       escreve nas leituras."""
    return sqlite3.connect(f'file:{CAMINHO_BANCO}?mode=ro', uri=True,
                           check_same_thread=False)


def ler_tabela(nome):
    with open(PASTA_TABELAS / f'{nome}.json', encoding='utf-8') as f:
        return json.load(f)


TAB_FATOR_CRISTA = ler_tabela('fator_crista')


def _para_float(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except TypeError:
        pass
    return round(float(v), 3)


def _padronizar_momento(serie):
    """Maio vem sem fuso e agosto vem com. Padroniza, como na
       função consultar da Sprint 4."""
    return pd.to_datetime(serie, format='mixed', errors='coerce',
                          utc=True).dt.tz_localize(None)


# ══════════════════════════════════════════════════════════════
#  AS FICHAS E AS REFERÊNCIAS
# ══════════════════════════════════════════════════════════════

def carregar_fichas():
    con = abrir_banco()
    linhas = con.execute('SELECT tag, ficha FROM ativos').fetchall()
    con.close()
    return {tag: json.loads(ficha) for tag, ficha in linhas}


def aprender_referencia(tag, dias=30):
    """Mesma conta da Sprint 4: o que é normal na diferença entre
       os sensores deste motor, olhando as leituras paradas da
       janela mais recente."""
    con = abrir_banco()
    dados = pd.read_sql_query(
        'SELECT momento, velocidade_1, temperatura_1, temperatura_2 '
        'FROM leituras WHERE tag = ?', con, params=[tag])
    con.close()
    if not len(dados):
        return None
    dados['momento'] = _padronizar_momento(dados['momento'])
    fim = dados['momento'].max()
    janela = dados[dados['momento'] >= fim - pd.Timedelta(days=dias)]
    parado = janela[janela['velocidade_1'] < LIMIAR_PARADO]
    if len(parado) < 20 or parado['temperatura_2'].isna().all():
        return {'suficiente': False}
    delta = parado['temperatura_2'] - parado['temperatura_1']
    return {'suficiente': True,
            'desvio_entre_sensores': round(float(delta.median()), 2),
            'leituras_paradas': int(len(parado))}


# ══════════════════════════════════════════════════════════════
#  A REPRODUÇÃO
#
#  Cada motor tem o seu histórico carregado na memória. O relógio
#  começa quando a API sobe. A leitura "atual" é a última que já
#  teria chegado naquele instante.
# ══════════════════════════════════════════════════════════════

class Reproducao:
    def __init__(self, tag, config, velocidade=1.0, inicio_s=0.0):
        self.tag = tag
        self.origem = config['origem']
        self.descricao = config['descricao']
        self.campos_esperados = config['campos_esperados']
        self.velocidade = velocidade

        con = abrir_banco()
        dados = pd.read_sql_query(
            'SELECT * FROM leituras WHERE tag = ? AND origem = ?',
            con, params=[tag, self.origem])
        con.close()
        dados['momento'] = _padronizar_momento(dados['momento'])
        dados = (dados.dropna(subset=['momento'])
                 .sort_values('momento').reset_index(drop=True))

        self.dados = dados
        self.offsets = ((dados['momento'] - dados['momento'].iloc[0])
                        .dt.total_seconds().to_numpy())
        # o ciclo termina um intervalo típico depois da última leitura,
        # para a volta ao começo não parecer uma leitura duplicada
        passo = float(np.median(np.diff(self.offsets)))
        self.ciclo = float(self.offsets[-1] + passo)
        self.ligada_em = time.time()
        self.inicio_s = float(inicio_s) % self.ciclo

    def tempo_virtual(self, agora=None):
        """Quantos segundos de histórico já 'passaram'."""
        agora = agora or time.time()
        return self.inicio_s + (agora - self.ligada_em) * self.velocidade

    def _posicao(self, virtual):
        no_ciclo = virtual % self.ciclo
        pos = int(np.searchsorted(self.offsets, no_ciclo, side='right')) - 1
        return max(pos, 0), no_ciclo

    def atual(self):
        agora = time.time()
        virtual = self.tempo_virtual(agora)
        pos, no_ciclo = self._posicao(virtual)
        idade_virtual = max(no_ciclo - self.offsets[pos], 0.0)
        idade_real = idade_virtual / self.velocidade
        linha = self.dados.iloc[pos]
        chegou_em = datetime.now(timezone.utc) - timedelta(
            seconds=idade_real)
        return linha, chegou_em, idade_real

    def janela(self, minutos, limite):
        """As leituras que chegaram nos últimos N minutos."""
        agora_utc = datetime.now(timezone.utc)
        virtual = self.tempo_virtual()
        largura = minutos * 60 * self.velocidade
        de = virtual - largura

        pedacos = []
        for volta in range(int(de // self.ciclo), int(virtual // self.ciclo) + 1):
            base_volta = volta * self.ciclo
            absoluto = base_volta + self.offsets
            filtro = (absoluto > de) & (absoluto <= virtual)
            if filtro.any():
                pedaco = self.dados[filtro].copy()
                atras = (virtual - absoluto[filtro]) / self.velocidade
                pedaco['chegou_em'] = [agora_utc - timedelta(seconds=float(s))
                                       for s in atras]
                pedacos.append(pedaco)

        if not pedacos:
            return pd.DataFrame()
        janela = pd.concat(pedacos, ignore_index=True)
        if len(janela) > limite:
            # mantém o desenho do período inteiro, pulando leituras
            indices = np.linspace(0, len(janela) - 1, limite).astype(int)
            janela = janela.iloc[indices]
        return janela


# ══════════════════════════════════════════════════════════════
#  OS OLHARES DA NORMA
#
#  Copiados da Sprint 4. Cada olhar devolve cor, motivo e de onde
#  veio. A cor final é a pior delas.
# ══════════════════════════════════════════════════════════════

ORDEM_COR = {'cinza': -1, 'verde': 0, 'amarelo': 1, 'vermelho': 2}
ACAO = {'vermelho': 'agir agora', 'amarelo': 'acompanhar de perto',
        'verde': 'nenhuma ação', 'cinza': 'instalar o sensor'}


def fase_do_motor(linha):
    v = linha.get('velocidade_1')
    if v is None or pd.isna(v):
        return 'sem dado'
    return 'parado' if v < LIMIAR_PARADO else 'girando'


def olhar_vibracao(linha, limites):
    v = linha.get('velocidade_1')
    if v is None or pd.isna(v):
        return ('verde', 'sem leitura de vibração')
    if v >= limites['parar_acima']['valor']:
        return ('vermelho', f"vibração em {v:.2f} mm/s, acima do limite "
                f"de {limites['parar_acima']['valor']:.1f}")
    if v >= limites['investigar_acima']['valor']:
        return ('amarelo', f"vibração em {v:.2f} mm/s, acima de "
                f"{limites['investigar_acima']['valor']:.1f}")
    if v >= limites['zona_b_ate']:
        return ('amarelo', f"vibração em {v:.2f} mm/s, acima da faixa "
                f"normal de {limites['zona_b_ate']:.1f}")
    return ('verde', f'vibração em {v:.2f} mm/s, dentro do normal')


def olhar_pico(linha):
    v = linha.get('velocidade_1')
    acel, pico = linha.get('aceleracao_1'), linha.get('pico_1')
    if v is None or pd.isna(v) or v < LIMIAR_CARGA_ALTA:
        return ('verde', 'este número só vale com o motor em carga '
                'alta e estável')
    if acel is None or pico is None or pd.isna(acel) or pd.isna(pico) \
            or acel <= 0:
        return ('verde', 'este sensor não entrega o pico')
    crista = pico / acel
    lim = TAB_FATOR_CRISTA['por_situacao']['carga_alta_estavel']
    if crista > lim['alerta_acima']:
        return ('vermelho', f'pico dividido pela média em {crista:.1f}, '
                'bem acima do normal')
    if crista > lim['normal_ate']:
        return ('amarelo', f'pico dividido pela média em {crista:.1f}, '
                'acima da faixa boa')
    return ('verde', f'pico dividido pela média em {crista:.1f}, faixa boa')


def olhar_temperatura(linha, ficha):
    t = linha.get('temperatura_1')
    if t is None or pd.isna(t):
        return ('verde', 'sem leitura de temperatura')
    maxima = ficha['derivado']['termico']['temperatura_maxima']['valor']
    if t >= maxima * 0.60:
        return ('vermelho', f'superfície em {t:.0f} graus, perto do '
                'limite do fio')
    if t >= maxima * 0.45:
        return ('amarelo', f'superfície em {t:.0f} graus, acima do esperado')
    return ('verde', f'superfície em {t:.0f} graus')


def olhar_diferenca_entre_lados(linha, referencia):
    t1, t2 = linha.get('temperatura_1'), linha.get('temperatura_2')
    if t1 is None or t2 is None or pd.isna(t1) or pd.isna(t2):
        return ('verde', 'este motor tem um sensor só')
    diferenca = t2 - t1
    if not referencia or not referencia.get('suficiente'):
        if abs(diferenca) >= 12:
            return ('vermelho', f'diferença de {diferenca:.0f} graus '
                    'entre os lados')
        if abs(diferenca) >= 9:
            return ('amarelo', f'diferença de {diferenca:.0f} graus '
                    'entre os lados')
        return ('verde', f'diferença de {diferenca:.0f} graus entre os lados')
    afastamento = diferenca - referencia['desvio_entre_sensores']
    if abs(afastamento) >= 8:
        return ('vermelho', f'a diferença entre os lados está '
                f'{afastamento:+.0f} graus fora do normal deste motor')
    if abs(afastamento) >= 5:
        return ('amarelo', f'a diferença entre os lados está '
                f'{afastamento:+.0f} graus fora do normal deste motor')
    return ('verde', f'diferença de {diferenca:.0f} graus, dentro do '
            'normal deste motor')


def avaliar(linha, ficha, base, referencia):
    limites = ficha['derivado']['vibracao'][f'base_{base}']
    olhares = {
        'vibração': olhar_vibracao(linha, limites),
        'pico': olhar_pico(linha),
        'temperatura': olhar_temperatura(linha, ficha),
        'diferença entre os lados': olhar_diferenca_entre_lados(
            linha, referencia),
    }
    pior = max(olhares, key=lambda n: ORDEM_COR[olhares[n][0]])
    cor, causa = olhares[pior]
    return {
        'cor': cor, 'acao': ACAO[cor], 'causa': causa,
        'o_que_puxou': pior,
        'olhares': {n: {'cor': c, 'motivo': m}
                    for n, (c, m) in olhares.items()},
    }


def resumo_limites(ficha, base):
    lim = ficha['derivado']['vibracao'][f'base_{base}']
    return {'base': base,
            'normal_ate_mm_s': lim['zona_b_ate'],
            'investigar_acima_mm_s': lim['investigar_acima']['valor'],
            'parar_acima_mm_s': lim['parar_acima']['valor'],
            'fonte': lim['parar_acima']['fonte']}


def completude(linha, esperados):
    preenchidos = [c for c in esperados
                   if linha.get(c) is not None and not pd.isna(linha.get(c))]
    faltando = [c for c in esperados if c not in preenchidos]
    pct = 100.0 * len(preenchidos) / len(esperados) if esperados else 100.0
    return {'esperados': len(esperados), 'preenchidos': len(preenchidos),
            'pct': round(pct, 1), 'faltando': faltando}


# ══════════════════════════════════════════════════════════════
#  O PROVIDER
# ══════════════════════════════════════════════════════════════

class ProviderSensores:
    def __init__(self, velocidade=1.0, inicio_s=0.0):
        self.fichas = carregar_fichas()
        self.reproducoes = {tag: Reproducao(tag, cfg, velocidade, inicio_s)
                            for tag, cfg in FONTES.items()
                            if tag in self.fichas}
        self.referencias = {tag: aprender_referencia(tag)
                            for tag in self.fichas}

    def tags(self):
        saida = []
        for tag, ficha in self.fichas.items():
            placa = ficha.get('placa', {})
            rep = self.reproducoes.get(tag)
            saida.append({
                'tag': tag,
                'descricao': f"{placa.get('modelo', '')} "
                             f"{placa.get('potencia_cv', '')} "
                             f"{placa.get('carcaca', '')}".strip(),
                'tem_sensor': rep is not None,
                'fonte': rep.descricao if rep else 'sem sensor instalado',
                'e_referencia': bool(ficha.get('e_referencia', False)),
            })
        return saida

    def existe(self, tag):
        return tag in self.fichas

    def leitura_atual(self, tag, base='rigida'):
        ficha = self.fichas[tag]
        rep = self.reproducoes.get(tag)
        if rep is None:
            return {
                'tag': tag, 'tem_leitura': False,
                'mensagem': 'este motor ainda não tem sensor instalado',
                'severidade': {'cor': 'cinza', 'acao': ACAO['cinza'],
                               'causa': 'esperando a primeira leitura',
                               'o_que_puxou': '', 'olhares': {}},
                'limites': resumo_limites(ficha, base),
            }

        linha, chegou_em, idade = rep.atual()
        linha = linha.to_dict()
        fase = fase_do_motor(linha)
        valores = {c: _para_float(linha.get(c)) for c in rep.campos_esperados}
        return {
            'tag': tag, 'tem_leitura': True,
            'momento': chegou_em.isoformat(timespec='milliseconds'),
            'momento_original': linha['momento'].isoformat(),
            'idade_dado_s': round(idade, 2),
            'origem': rep.origem, 'fonte': rep.descricao,
            'fase': fase,
            'leitura': valores,
            'unidades': {c: GRANDEZAS[c] for c in rep.campos_esperados},
            'severidade': avaliar(linha, ficha, base,
                                  self.referencias.get(tag)),
            'limites': resumo_limites(ficha, base),
            'completude': completude(linha, rep.campos_esperados),
        }

    def historico_ao_vivo(self, tag, minutos, limite, base='rigida'):
        rep = self.reproducoes.get(tag)
        ficha = self.fichas[tag]
        if rep is None:
            return {'tag': tag, 'origem': None, 'quantidade': 0,
                    'leituras': [], 'limites': resumo_limites(ficha, base),
                    'completude_media_pct': None}
        janela = rep.janela(minutos, limite)
        return self._montar_historico(tag, janela, rep.origem,
                                      rep.campos_esperados, ficha, base,
                                      momento_col='chegou_em')

    def historico_gravado(self, tag, de, ate, limite, base='rigida'):
        """O que está guardado na base, com as datas originais. É por
           aqui que aparece a coleta de agosto pelo endereço da
           fábrica."""
        ficha = self.fichas[tag]
        con = abrir_banco()
        sql = 'SELECT * FROM leituras WHERE tag = ?'
        dados = pd.read_sql_query(sql, con, params=[tag])
        con.close()
        if not len(dados):
            return {'tag': tag, 'origem': None, 'quantidade': 0,
                    'leituras': [], 'limites': resumo_limites(ficha, base),
                    'completude_media_pct': None}
        dados['momento'] = _padronizar_momento(dados['momento'])
        dados = dados.dropna(subset=['momento']).sort_values('momento')
        if de is not None:
            dados = dados[dados['momento'] >= pd.Timestamp(de).tz_localize(None)]
        if ate is not None:
            dados = dados[dados['momento'] <= pd.Timestamp(ate).tz_localize(None)]
        if len(dados) > limite:
            indices = np.linspace(0, len(dados) - 1, limite).astype(int)
            dados = dados.iloc[indices]
        origens = sorted(dados['origem'].dropna().unique().tolist())
        esperados = CAMPOS_ESPERADOS_GRAVADO.get(
            origens[0] if origens else '', FONTES.get(tag, {}).get(
                'campos_esperados', []))
        return self._montar_historico(tag, dados, ', '.join(origens),
                                      esperados, ficha, base,
                                      momento_col='momento')

    def _montar_historico(self, tag, dados, origem, esperados, ficha, base,
                          momento_col):
        leituras = []
        soma_completude = 0.0
        for _, r in dados.iterrows():
            linha = r.to_dict()
            comp = completude(linha, CAMPOS_ESPERADOS_GRAVADO.get(
                linha.get('origem'), esperados))
            soma_completude += comp['pct']
            momento = linha[momento_col]
            leituras.append({
                'momento': (momento.isoformat(timespec='milliseconds')
                            if hasattr(momento, 'isoformat') else str(momento)),
                'momento_original': linha['momento'].isoformat()
                if hasattr(linha['momento'], 'isoformat') else str(linha['momento']),
                'origem': linha.get('origem'),
                'fase': fase_do_motor(linha),
                **{c: _para_float(linha.get(c)) for c in esperados},
                'completude_pct': comp['pct'],
            })
        n = len(leituras)
        return {
            'tag': tag, 'origem': origem, 'quantidade': n,
            'campos': esperados,
            'unidades': {c: GRANDEZAS[c] for c in esperados},
            'completude_media_pct': round(soma_completude / n, 1) if n else None,
            'limites': resumo_limites(ficha, base),
            'leituras': leituras,
        }
