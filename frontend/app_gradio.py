# ============================================================
#   DUO · INTERFACE DO CHECKPOINT
#
#   Três abas, uma para cada endpoint:
#     leitura atual    GET /v1/sensores/{tag}/leitura-atual
#     histórico        GET /v1/sensores/{tag}/historico
#     observabilidade  GET /v1/observabilidade
#
#   A tela não lê banco nenhum. Tudo chega pela API, via
#   requests, com os headers de contexto. Cada aba manda o
#   próprio X-Feature e cada pessoa que abre a tela ganha o
#   próprio X-Session-Id.
#
#   Para rodar (com a API já ligada):
#       python -m frontend.app_gradio
# ============================================================

import math
import os
import uuid

import gradio as gr
import pandas as pd
import plotly.graph_objects as go

from frontend.cliente_api import ClienteDUO

URL_API = os.environ.get('DUO_API_URL', 'http://127.0.0.1:8000')

# ── as cores do modo claro da Sprint 4 ────────────────────
C = {
    'fundo': '#F4F2FA', 'painel': '#FFFFFF', 'borda': '#D6CFEC',
    'grade': '#ECE8F6',
    'texto': '#1A0F35', 'texto2': '#5A4B8C', 'texto3': '#8B7EB5',
    'roxo': '#2F008C', 'ciano': '#00959B', 'ambar': '#C77F00',
    'verde': '#128040', 'amarelo': '#B87400', 'vermelho': '#C62333',
    'cinza': '#8B7EB5',
}
# cores de identidade (séries), separadas das cores de estado
SERIE = ['#2a78d6', '#eb6834', '#1baf7a']

SITUACAO = {
    'ok': ('verde', 'dentro do contrato'),
    'atencao': ('amarelo', 'atenção'),
    'estouro': ('vermelho', 'limiar estourado'),
    'sem_dado': ('cinza', 'sem dado ainda'),
    'sem_baseline': ('cinza', 'sem baseline'),
    'sem_limiar': ('cinza', 'sem limiar'),
}
ROTULO_COR = {'verde': 'normal', 'amarelo': 'precisa olhar',
              'vermelho': 'não deve operar', 'cinza': 'sem medição'}

_clientes = {}


def cliente(session_id):
    if session_id not in _clientes:
        _clientes[session_id] = ClienteDUO(URL_API, session_id=session_id)
    return _clientes[session_id]


# ══════════════════════════════════════════════════════════════
#  PEDAÇOS DE TELA
# ══════════════════════════════════════════════════════════════

def forma(estado, cor, tamanho=16):
    """Círculo é normal, triângulo é aviso, octógono é parar e
       círculo vazado é sem medição. Quem não distingue as cores
       lê a forma."""
    m = tamanho / 2
    if estado == 'verde':
        d = f"<circle cx='{m}' cy='{m}' r='{m-1.5}' fill='{cor}'/>"
    elif estado == 'amarelo':
        d = (f"<polygon points='{m},1.5 {tamanho-1},{tamanho-2} "
             f"1,{tamanho-2}' fill='{cor}'/>")
    elif estado == 'vermelho':
        r = m - 1.5
        pts = ' '.join(f"{m + r*math.cos(math.pi/8 + i*math.pi/4):.1f},"
                       f"{m + r*math.sin(math.pi/8 + i*math.pi/4):.1f}"
                       for i in range(8))
        d = f"<polygon points='{pts}' fill='{cor}'/>"
    else:
        d = (f"<circle cx='{m}' cy='{m}' r='{m-2}' fill='none' "
             f"stroke='{cor}' stroke-width='2'/>")
    return (f"<svg width='{tamanho}' height='{tamanho}' "
            f"viewBox='0 0 {tamanho} {tamanho}' style='vertical-align:"
            f"middle;flex:none'>{d}</svg>")


def titulo_secao(texto):
    return (f"<div style='color:{C['roxo']};font-size:11px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:1.2px;border-bottom:"
            f"1px solid {C['borda']};padding-bottom:5px;margin:18px 0 8px'>"
            f"{texto}</div>")


def aviso(texto, cor='cinza'):
    return (f"<div style='display:flex;gap:10px;align-items:center;"
            f"padding:14px;border:1px solid {C['borda']};border-radius:8px;"
            f"background:{C['painel']};color:{C['texto2']}'>"
            f"{forma(cor, C[cor], 18)}<span>{texto}</span></div>")


def rodape_chamada(meta):
    if not meta:
        return ''
    return (f"<div style='font-size:11px;color:{C['texto3']};margin-top:14px'>"
            f"request_id {meta.get('request_id') or 'sem id'} · status "
            f"{meta.get('status')} · {meta.get('latencia_api_ms', 0):.1f} ms na "
            f"API · {meta.get('latencia_front_ms', 0):.1f} ms até a tela</div>")


CABECALHO = f"""
<div style="display:flex;align-items:center;gap:20px;padding:18px 22px;
            background:linear-gradient(100deg,{C['roxo']} 0%,#6B4FC4 100%);
            border-radius:12px;border-left:6px solid {C['ambar']}">
  <div style="width:64px;height:64px;border:2px solid #8FF0F4;border-radius:12px;
              display:flex;align-items:center;justify-content:center;
              color:#8FF0F4;font-size:22px;font-weight:800">W22</div>
  <div>
    <div style="font-size:30px;font-weight:800;color:#FFFFFF;letter-spacing:1px;
                line-height:1.05">DUO<span style="color:#FFD166">:</span>
      <span style="color:#8FF0F4">sensores sob observação</span></div>
    <div style="font-size:13px;color:#E4DDF7;margin-top:6px;max-width:720px">
      Cada consulta ao sensor fica registrada: quem pediu, de qual tela,
      quanto demorou e se a leitura chegou fresca e completa.</div>
  </div>
</div>"""


# ══════════════════════════════════════════════════════════════
#  ABA 1 · LEITURA ATUAL
# ══════════════════════════════════════════════════════════════

def tela_leitura(session_id, tag, base):
    corpo, meta = cliente(session_id).leitura_atual(tag, base)
    if not meta['ok']:
        return aviso(f"A leitura não veio: {meta.get('erro')}", 'vermelho') + \
            rodape_chamada(meta)

    sev = corpo['severidade']
    cor = sev['cor']
    faixa = (f"<div style='background:{C[cor]};color:#FFFFFF;padding:14px;"
             f"border-radius:8px;display:flex;align-items:center;"
             f"justify-content:center;gap:12px;font-size:18px;font-weight:700'>"
             f"{forma(cor, '#FFFFFF', 22)} {sev['acao'].upper()}"
             f"<span style='font-weight:400;font-size:13px;opacity:.9'>"
             f"({ROTULO_COR[cor]})</span></div>")

    if not corpo['tem_leitura']:
        return (faixa + f"<div style='margin-top:12px;font-size:15px'>"
                f"{corpo['mensagem']}</div>" + rodape_chamada(meta))

    fr = corpo['freshness']
    cor_fr = 'verde' if fr['situacao'] == 'em dia' else 'amarelo'
    comp = corpo['completude']
    cor_comp = 'verde' if comp['pct'] >= 100 else (
        'amarelo' if comp['pct'] >= 75 else 'vermelho')

    def selo(rotulo, valor, cor_s, nota=''):
        marca = forma(cor_s, C[cor_s], 14) if cor_s else ''
        return (f"<div style='flex:1;min-width:150px;border:1px solid "
                f"{C['borda']};border-radius:8px;padding:10px 12px;"
                f"background:{C['painel']}'>"
                f"<div style='font-size:11px;color:{C['texto3']};"
                f"text-transform:uppercase;letter-spacing:1px'>{rotulo}</div>"
                f"<div style='display:flex;align-items:center;gap:8px;"
                f"margin-top:4px;font-size:18px;font-weight:700;color:"
                f"{C['texto']}'>{marca}{valor}</div>"
                f"<div style='font-size:11px;color:{C['texto2']}'>{nota}</div>"
                f"</div>")

    selos = (
        "<div style='display:flex;gap:10px;flex-wrap:wrap;margin-top:12px'>"
        + selo('idade da leitura', f"{fr['idade_s']:.1f} s", cor_fr,
               f"limite {fr['limite_s']:g} s com o motor {corpo['fase']}")
        + selo('completude', f"{comp['preenchidos']} de {comp['esperados']}",
               cor_comp, 'faltando: ' + ', '.join(comp['faltando'])
               if comp['faltando'] else 'todos os campos chegaram')
        + selo('fase do motor', corpo['fase'], None, corpo['origem'])
        + "</div>")

    linhas_olhar = ''.join(
        f"<tr><td style='padding:6px 8px;width:24px'>"
        f"{forma(o['cor'], C[o['cor']], 14)}</td>"
        f"<td style='padding:6px 8px;color:{C['texto']};width:170px'>{n}</td>"
        f"<td style='padding:6px 8px;color:{C['texto2']};font-size:12.5px'>"
        f"{o['motivo']}</td></tr>"
        for n, o in sev['olhares'].items())

    valores = ''.join(
        f"<div style='border:1px solid {C['borda']};border-radius:6px;"
        f"padding:6px 10px;background:{C['painel']}'>"
        f"<div style='font-size:10.5px;color:{C['texto3']}'>{campo}</div>"
        f"<div style='font-size:15px;font-weight:600;color:{C['texto']}'>"
        f"{'vazio' if v is None else v} <span style='font-size:11px;"
        f"color:{C['texto2']}'>{corpo['unidades'][campo]}</span></div></div>"
        for campo, v in corpo['leitura'].items())

    lim = corpo['limites']
    return (
        faixa
        + f"<div style='margin-top:12px;font-size:15px;color:{C['texto']}'>"
          f"{sev['causa']}</div>"
        + selos
        + titulo_secao('o que a regra olhou')
        + f"<table style='width:100%;border-collapse:collapse'>{linhas_olhar}"
          f"</table>"
        + titulo_secao('a leitura que chegou')
        + f"<div style='display:grid;grid-template-columns:repeat(auto-fill,"
          f"minmax(140px,1fr));gap:8px'>{valores}</div>"
        + f"<div style='font-size:11.5px;color:{C['texto2']};margin-top:12px'>"
          f"limites de vibração na base {lim['base']}: normal até "
          f"{lim['normal_ate_mm_s']} · olhar acima de "
          f"{lim['investigar_acima_mm_s']} · parar acima de "
          f"{lim['parar_acima_mm_s']} mm/s ({lim['fonte']})</div>"
        + f"<div style='font-size:11px;color:{C['texto3']}'>chegou em "
          f"{corpo['momento']} · no histórico original era "
          f"{corpo['momento_original']}</div>"
        + rodape_chamada(meta))


# ══════════════════════════════════════════════════════════════
#  GRÁFICOS
# ══════════════════════════════════════════════════════════════

def _layout(fig, titulo, eixo_y, altura=330, log=False):
    fig.update_layout(
        title=dict(text=titulo, font=dict(color=C['texto'], size=14)),
        paper_bgcolor=C['painel'], plot_bgcolor=C['painel'],
        font=dict(color=C['texto2'], family='Segoe UI, Arial'),
        height=altura, margin=dict(l=56, r=24, t=48, b=40),
        xaxis=dict(gridcolor=C['grade'], linecolor=C['borda']),
        yaxis=dict(title=eixo_y, gridcolor=C['grade'], linecolor=C['borda'],
                   type='log' if log else '-',
                   dtick=1 if log else None,
                   rangemode='tozero' if not log else None),
        legend=dict(orientation='h', y=1.02, x=1, xanchor='right',
                    yanchor='bottom'),
        hovermode='x unified')
    return fig


def _vazio(texto):
    fig = go.Figure()
    fig.add_annotation(text=texto, showarrow=False,
                       font=dict(size=14, color=C['texto3']))
    fig.update_layout(paper_bgcolor=C['painel'], plot_bgcolor=C['painel'],
                      height=300, xaxis=dict(visible=False),
                      yaxis=dict(visible=False))
    return fig


def _linha_limite(fig, valor, rotulo, cor, log=False):
    """Linha tracejada de limiar. O rótulo vai à parte porque, em
       eixo logarítmico, a anotação usa o log do valor."""
    fig.add_hline(y=valor, line=dict(color=cor, dash='dash', width=1))
    fig.add_annotation(xref='paper', x=0, yref='y',
                       y=math.log10(valor) if log else valor,
                       text=rotulo, showarrow=False, xanchor='left',
                       yanchor='bottom',
                       font=dict(color=C['texto2'], size=10))


# ══════════════════════════════════════════════════════════════
#  ABA 2 · HISTÓRICO
# ══════════════════════════════════════════════════════════════

PERIODOS = {'tudo o que está gravado': (None, None),
            'maio, arquivo exportado': ('2026-05-01', '2026-05-31'),
            'agosto, endereço da fábrica': ('2026-08-01', '2026-08-31')}


def tela_historico(session_id, tag, base, fonte, minutos, periodo):
    de, ate = PERIODOS.get(periodo, (None, None))
    corpo, meta = cliente(session_id).historico(
        tag, minutos=minutos, base=base,
        fonte='gravado' if fonte.startswith('gravado') else 'ao_vivo',
        de=de, ate=ate)
    if not meta['ok']:
        return (aviso(f"O histórico não veio: {meta.get('erro')}", 'vermelho')
                + rodape_chamada(meta), _vazio('sem dado'), _vazio('sem dado'),
                pd.DataFrame())
    if not corpo['quantidade']:
        return (aviso('Nenhuma leitura neste período. Este motor pode não ter '
                      'sensor instalado.') + rodape_chamada(meta),
                _vazio('sem leitura'), _vazio('sem leitura'), pd.DataFrame())

    t = pd.DataFrame(corpo['leituras'])
    t['momento'] = pd.to_datetime(t['momento'], format='mixed', utc=True)
    lim = corpo['limites']

    fig_v = go.Figure()
    for i, campo in enumerate(['velocidade_1', 'velocidade_2']):
        if campo in t and t[campo].notna().any():
            fig_v.add_trace(go.Scatter(
                x=t['momento'], y=t[campo], mode='lines',
                line=dict(width=2, color=SERIE[i]),
                name=f"sensor {campo[-1]}",
                hovertemplate='%{y:.2f} mm/s'))
    _linha_limite(fig_v, lim['normal_ate_mm_s'],
                  f"normal até {lim['normal_ate_mm_s']}", C['verde'])
    _linha_limite(fig_v, lim['investigar_acima_mm_s'],
                  f"olhar acima de {lim['investigar_acima_mm_s']}", C['amarelo'])
    _linha_limite(fig_v, lim['parar_acima_mm_s'],
                  f"parar acima de {lim['parar_acima_mm_s']}", C['vermelho'])
    _layout(fig_v, f"{tag} · vibração · {corpo['quantidade']:,} leituras",
            'mm/s')

    fig_t = go.Figure()
    for i, campo in enumerate(['temperatura_1', 'temperatura_2']):
        if campo in t and t[campo].notna().any():
            fig_t.add_trace(go.Scatter(
                x=t['momento'], y=t[campo], mode='lines',
                line=dict(width=2, color=SERIE[i]),
                name=f"sensor {campo[-1]}", hovertemplate='%{y:.1f} °C'))
    if not fig_t.data:
        fig_t = _vazio('esta fonte não traz temperatura')
    else:
        _layout(fig_t, f'{tag} · temperatura da superfície', '°C', altura=260)
        fig_t.update_yaxes(rangemode='normal')

    resumo = (
        f"<div style='display:flex;gap:18px;flex-wrap:wrap;font-size:13px;"
        f"color:{C['texto2']}'>"
        f"<span><b style='color:{C['texto']}'>{corpo['quantidade']:,}</b> "
        f"leituras</span>"
        f"<span>origem: <b style='color:{C['texto']}'>{corpo['origem']}</b></span>"
        f"<span>completude média: <b style='color:{C['texto']}'>"
        f"{corpo['completude_media_pct']}%</b></span></div>"
        + rodape_chamada(meta))

    tabela = t.copy()
    tabela['momento'] = tabela['momento'].dt.strftime('%d/%m %H:%M:%S')
    colunas = ['momento', 'fase'] + corpo['campos'] + ['completude_pct']
    return resumo, fig_v, fig_t, tabela[colunas].tail(200).iloc[::-1]


# ══════════════════════════════════════════════════════════════
#  ABA 3 · OBSERVABILIDADE
# ══════════════════════════════════════════════════════════════

JANELAS = {'últimos 15 minutos': 15, 'última hora': 60,
           'últimas 24 horas': 1440, 'tudo': None}


def cartoes(indicadores):
    blocos = ''
    for chave, ind in indicadores.items():
        cor, rotulo = SITUACAO.get(ind['situacao'], ('cinza', ind['situacao']))
        valor = 'sem dado' if ind['valor'] is None else f"{ind['valor']:g}"
        base = ind.get('baseline')
        linha_base = (f"baseline {base:g} {ind['unidade']}"
                      if isinstance(base, (int, float)) else 'baseline a medir')
        acao = (f"<div style='font-size:11px;color:{C['texto']};margin-top:6px;"
                f"border-top:1px solid {C['borda']};padding-top:6px'>"
                f"{ind['acao']}</div>" if ind.get('acao') else '')
        blocos += (
            f"<div style='border:1px solid {C['borda']};border-left:4px solid "
            f"{C[cor]};border-radius:8px;padding:10px 12px;"
            f"background:{C['painel']}'>"
            f"<div style='font-size:11.5px;color:{C['texto2']};min-height:30px'>"
            f"{ind['nome']}</div>"
            f"<div style='font-size:24px;font-weight:700;color:{C['texto']}'>"
            f"{valor} <span style='font-size:12px;font-weight:400;color:"
            f"{C['texto2']}'>{ind['unidade']}</span></div>"
            f"<div style='display:flex;align-items:center;gap:6px;font-size:"
            f"11.5px;color:{C['texto2']};margin-top:2px'>"
            f"{forma(cor, C[cor], 12)}{rotulo}</div>"
            f"<div style='font-size:10.5px;color:{C['texto3']}'>{linha_base}"
            f"</div>{acao}</div>")
    return (f"<div style='display:grid;grid-template-columns:repeat(auto-fill,"
            f"minmax(210px,1fr));gap:10px'>{blocos}</div>")


def graficos_observabilidade(reg, contrato):
    if reg.empty:
        v = _vazio('nenhuma chamada registrada nesta janela')
        return v, v, v, v, v
    reg = reg.copy()
    reg['momento'] = pd.to_datetime(reg['momento'])
    reg = reg.sort_values('momento')
    reg['endpoint'] = reg['rota'].str.replace('/v1/sensores/{tag}/', '',
                                              regex=False)

    # latência por endpoint
    ind = contrato['indicadores']['latencia_p95_ms']
    fig_lat = go.Figure()
    for i, (nome, g) in enumerate(reg.groupby('endpoint')):
        fig_lat.add_trace(go.Scatter(
            x=g['momento'], y=g['latencia_ms'], mode='markers',
            marker=dict(size=8, color=SERIE[i % 3], opacity=.75,
                        line=dict(width=1, color=C['painel'])),
            name=nome, hovertemplate='%{y:.1f} ms'))
    if isinstance(ind.get('atencao'), (int, float)):
        _linha_limite(fig_lat, ind['atencao'], f"atenção {ind['atencao']} ms",
                      C['amarelo'], log=True)
        _linha_limite(fig_lat, ind['estouro'], f"estouro {ind['estouro']} ms",
                      C['vermelho'], log=True)
    _layout(fig_lat, 'Tempo de resposta de cada chamada', 'ms', log=True)

    # freshness de cada leitura atual
    atuais = reg[reg['idade_dado_s'].notna()]
    if atuais.empty:
        fig_fr = _vazio('nenhuma leitura atual nesta janela')
    else:
        fig_fr = go.Figure()
        for i, ((tag, fase), g) in enumerate(atuais.groupby(['tag', 'fase'])):
            fig_fr.add_trace(go.Scatter(
                x=g['momento'], y=g['idade_dado_s'].clip(lower=0.05),
                mode='markers', name=f'{tag} {fase}',
                marker=dict(size=8, color=SERIE[i % 3], opacity=.75,
                            line=dict(width=1, color=C['painel'])),
                customdata=g[['tag', 'limite_freshness_s']],
                hovertemplate='%{y:.2f} s · %{customdata[0]} · limite '
                              '%{customdata[1]} s'))
        for (tag, fase), g in atuais.groupby(['origem', 'fase']):
            lim = g['limite_freshness_s'].iloc[0]
            _linha_limite(fig_fr, lim, f'limite {tag}, {fase}: {lim:g} s',
                          C['amarelo'], log=True)
        _layout(fig_fr, 'Idade da leitura entregue (freshness)', 's',
                log=True)

    # completude de freshness por janela de 5 minutos
    if atuais.empty:
        fig_cf = _vazio('nenhuma leitura atual nesta janela')
    else:
        por_janela = (atuais.set_index('momento')['dentro_freshness']
                      .resample('5min').mean() * 100).dropna()
        fig_cf = go.Figure(go.Scatter(
            x=por_janela.index, y=por_janela.values, mode='lines+markers',
            line=dict(width=2, color=SERIE[0]), marker=dict(size=8),
            name='dentro do limite', hovertemplate='%{y:.1f}%'))
        regra = contrato['indicadores']['completude_freshness_pct']
        _linha_limite(fig_cf, regra['atencao'], f"atenção {regra['atencao']}%",
                      C['amarelo'])
        _linha_limite(fig_cf, regra['estouro'], f"estouro {regra['estouro']}%",
                      C['vermelho'])
        _layout(fig_cf, 'Taxa de completude de freshness, a cada 5 minutos',
                '%')
        fig_cf.update_yaxes(range=[min(80, por_janela.min() - 5), 101])
        fig_cf.update_layout(showlegend=False)

    # volume por feature
    vol = reg['feature'].fillna('(sem feature)').value_counts().sort_values()
    fig_vol = go.Figure(go.Bar(
        x=vol.values.tolist(), y=[str(i) for i in vol.index],
        orientation='h',
        marker=dict(color=SERIE[0], cornerradius=4),
        hovertemplate='%{x} chamadas'))
    _layout(fig_vol, 'Chamadas por feature (X-Feature)', '',
            altura=max(220, 60 + 34 * len(vol)))
    fig_vol.update_layout(hovermode='closest', showlegend=False)

    # severidade ao longo do tempo
    if atuais.empty:
        fig_sev = _vazio('nenhuma leitura atual nesta janela')
    else:
        sev = (atuais.set_index('momento').groupby(
            [pd.Grouper(freq='5min'), 'severidade']).size().unstack(fill_value=0))
        sev = sev.div(sev.sum(axis=1), axis=0) * 100
        fig_sev = go.Figure()
        for cor in ['verde', 'amarelo', 'vermelho', 'cinza']:
            if cor in sev:
                fig_sev.add_trace(go.Bar(
                    x=sev.index, y=sev[cor], name=ROTULO_COR[cor],
                    marker=dict(color=C[cor], line=dict(width=2,
                                                        color=C['painel'])),
                    hovertemplate='%{y:.0f}%'))
        _layout(fig_sev, 'Cor devolvida nas leituras atuais, a cada 5 minutos',
                '% das leituras')
        fig_sev.update_layout(barmode='stack')
    return fig_lat, fig_fr, fig_cf, fig_vol, fig_sev


def tela_observabilidade(session_id, janela, so_minha_sessao, incluir_painel):
    api = cliente(session_id)
    corpo, meta = api.observabilidade(
        limite=20000, minutos=JANELAS.get(janela),
        session_filtro=session_id if so_minha_sessao else None,
        incluir_painel=incluir_painel)
    contrato, _ = api.contrato()
    if not meta['ok'] or contrato is None:
        v = _vazio('sem resposta da API')
        return (aviso(f"O relatório não veio: {meta.get('erro')}", 'vermelho'),
                v, v, v, v, v, pd.DataFrame())

    reg = pd.DataFrame(corpo['registros'])
    topo = (f"<div style='font-size:13px;color:{C['texto2']};margin-bottom:10px'>"
            f"<b style='color:{C['texto']}'>{corpo['total_chamadas']:,}</b> "
            f"chamadas na janela · gerado em {corpo['gerado_em']} · "
            f"sua sessão: <code>{session_id}</code></div>")
    figs = graficos_observabilidade(reg, contrato)

    if not reg.empty:
        tabela = reg[['momento', 'session_id', 'feature', 'caminho', 'status',
                      'latencia_ms', 'idade_dado_s', 'completude_pct',
                      'severidade', 'request_id']].head(150)
    else:
        tabela = pd.DataFrame()
    return (topo + cartoes(corpo['indicadores']) + rodape_chamada(meta),
            *figs, tabela)


# ══════════════════════════════════════════════════════════════
#  A MONTAGEM
# ══════════════════════════════════════════════════════════════

ESTILO = f"""
.gradio-container {{ background:{C['fundo']} !important;
  font-family:'Segoe UI',Arial,sans-serif !important; }}
footer {{ display:none !important; }}
"""


def motores_disponiveis():
    corpo, meta = ClienteDUO(URL_API, session_id='tela-abertura').motores()
    if meta['ok']:
        return [(f"{m['tag']} · {m['descricao']}"
                 + ('' if m['tem_sensor'] else ' · sem sensor'), m['tag'])
                for m in corpo['motores']]
    return [('BBA-001', 'BBA-001')]


def nova_sessao():
    return f'tela-{uuid.uuid4().hex[:10]}'


def montar():
    opcoes = motores_disponiveis()
    with gr.Blocks(title='DUO · sensores sob observação',
                   analytics_enabled=False) as tela:
        sessao = gr.State()
        gr.HTML(CABECALHO)

        with gr.Row():
            motor = gr.Dropdown(choices=opcoes, value=opcoes[0][1],
                                label='motor', scale=3)
            base = gr.Radio(choices=[('rígida', 'rigida'),
                                     ('flexível', 'flexivel')],
                            value='rigida', label='tipo de base', scale=2,
                            info='a norma dá limites diferentes para cada uma')

        with gr.Tabs():
            with gr.Tab('leitura atual'):
                with gr.Row():
                    automatico = gr.Checkbox(
                        value=True, label='atualizar sozinho a cada 3 s',
                        info='como a tela da sala de controle')
                    botao_leitura = gr.Button('ler agora', variant='primary')
                painel_leitura = gr.HTML()
                relogio = gr.Timer(3)

            with gr.Tab('histórico'):
                with gr.Row():
                    fonte = gr.Radio(choices=['ao vivo', 'gravado na base'],
                                     value='ao vivo', label='de onde')
                    minutos = gr.Slider(5, 240, value=30, step=5,
                                        label='janela ao vivo (minutos)')
                    periodo = gr.Dropdown(choices=list(PERIODOS),
                                          value='tudo o que está gravado',
                                          label='período gravado')
                botao_hist = gr.Button('buscar histórico', variant='primary')
                resumo_hist = gr.HTML()
                graf_vib = gr.Plot()
                graf_temp = gr.Plot()
                tabela_hist = gr.Dataframe(label='últimas 200 leituras',
                                           interactive=False, max_height=360)

            with gr.Tab('observabilidade'):
                with gr.Row():
                    janela = gr.Dropdown(choices=list(JANELAS),
                                         value='última hora', label='janela')
                    so_minha = gr.Checkbox(value=False,
                                           label='só a minha sessão')
                    incluir = gr.Checkbox(
                        value=False, label='contar as chamadas deste painel')
                    botao_obs = gr.Button('atualizar relatório',
                                          variant='primary')
                painel_obs = gr.HTML()
                with gr.Row():
                    g_lat = gr.Plot()
                    g_fr = gr.Plot()
                with gr.Row():
                    g_cf = gr.Plot()
                    g_sev = gr.Plot()
                g_vol = gr.Plot()
                tabela_obs = gr.Dataframe(label='últimas chamadas registradas',
                                          interactive=False, max_height=360)

        gr.HTML(f"<div style='font-size:11px;color:{C['texto3']};"
                f"margin-top:8px'>API em {URL_API} · cada aba envia o seu "
                f"X-Feature e esta janela do navegador tem o seu "
                f"X-Session-Id</div>")

        # ── ligações ─────────────────────────────────────
        entradas_leitura = [sessao, motor, base]
        tela.load(nova_sessao, outputs=sessao).then(
            tela_leitura, entradas_leitura, painel_leitura)
        botao_leitura.click(tela_leitura, entradas_leitura, painel_leitura)
        motor.change(tela_leitura, entradas_leitura, painel_leitura)
        base.change(tela_leitura, entradas_leitura, painel_leitura)

        def tique(s, t, b, ligado):
            if not ligado or not s:
                return gr.skip()
            return tela_leitura(s, t, b)

        relogio.tick(tique, [sessao, motor, base, automatico], painel_leitura,
                     show_progress='hidden')

        botao_hist.click(tela_historico,
                         [sessao, motor, base, fonte, minutos, periodo],
                         [resumo_hist, graf_vib, graf_temp, tabela_hist])
        botao_obs.click(tela_observabilidade,
                        [sessao, janela, so_minha, incluir],
                        [painel_obs, g_lat, g_fr, g_cf, g_vol, g_sev,
                         tabela_obs])
    return tela


if __name__ == '__main__':
    tema = gr.themes.Base(primary_hue='purple', neutral_hue='slate')
    montar().launch(server_port=int(os.environ.get('DUO_PORTA_TELA', 7860)),
                    css=ESTILO, theme=tema)
