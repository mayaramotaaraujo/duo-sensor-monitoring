# ============================================================
#   DUO · RELATÓRIO DE OBSERVABILIDADE PARA A GOVERNANÇA
#
#   Pede à API os registros de um período e gera as figuras e
#   as tabelas que vão para o documento da disciplina. Também é
#   consumo via requests: a chamada sai com X-Feature igual a
#   relatorio_governanca.
#
#       python -m frontend.relatorio_observabilidade --nome baseline \
#           --de 2026-10-14T23:08:00 --ate 2026-10-14T23:38:00
#
#   Datas em UTC, como ficam gravadas.
# ============================================================

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from frontend.cliente_api import ClienteDUO

PASTA = Path(__file__).resolve().parent.parent / 'docs' / 'relatorio'

SERIE = ['#2a78d6', '#eb6834', '#1baf7a']
ESTADO = {'verde': '#128040', 'amarelo': '#B87400', 'vermelho': '#C62333',
          'cinza': '#8B7EB5'}
ROTULO_COR = {'verde': 'normal', 'amarelo': 'precisa olhar',
              'vermelho': 'não deve operar', 'cinza': 'sem medição'}
TEXTO, TEXTO2, GRADE = '#1A0F35', '#5A4B8C', '#E6E2F0'

plt.rcParams.update({
    'font.size': 9, 'axes.edgecolor': '#C9C2DD', 'axes.labelcolor': TEXTO2,
    'xtick.color': TEXTO2, 'ytick.color': TEXTO2, 'axes.titlesize': 10,
    'axes.titlecolor': TEXTO, 'axes.grid': True, 'grid.color': GRADE,
    'grid.linewidth': .6, 'axes.spines.top': False,
    'axes.spines.right': False, 'legend.frameon': False,
    'figure.dpi': 110, 'savefig.dpi': 220, 'savefig.bbox': 'tight',
})


def limiar(ax, valor, rotulo, cor):
    ax.axhline(valor, color=cor, linestyle='--', linewidth=1)
    ax.annotate(rotulo, xy=(0, valor), xycoords=('axes fraction', 'data'),
                xytext=(3, 2), textcoords='offset points', fontsize=7.5,
                color=TEXTO2, va='bottom')


def eixo_tempo(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.set_xlabel('horário (UTC)')


def gerar(nome, de, ate, url=None):
    api = ClienteDUO(url, session_id=f'relatorio-{nome}')
    corpo, meta = api.observabilidade(limite=20000, de=de, ate=ate,
                                      feature='relatorio_governanca')
    contrato, _ = api.contrato(feature='relatorio_governanca')
    if not meta['ok']:
        raise SystemExit(f"a API não respondeu: {meta.get('erro')}")

    pasta = PASTA / nome
    pasta.mkdir(parents=True, exist_ok=True)
    reg = pd.DataFrame(corpo['registros'])
    reg.to_csv(pasta / 'chamadas.csv', index=False)
    with open(pasta / 'indicadores.json', 'w', encoding='utf-8') as f:
        json.dump({'periodo': {'de': de, 'ate': ate},
                   'total_chamadas': corpo['total_chamadas'],
                   'indicadores': corpo['indicadores']}, f,
                  ensure_ascii=False, indent=2)
    if reg.empty:
        print('  nenhuma chamada no período')
        return

    reg['momento'] = pd.to_datetime(reg['momento'])
    reg = reg.sort_values('momento')
    reg['endpoint'] = reg['rota'].str.replace('/v1/sensores/{tag}/', '',
                                              regex=False)
    atuais = reg[reg['idade_dado_s'].notna()].copy()
    ind = contrato['indicadores']

    # ── figura 1: latência p95 por minuto e endpoint ──────
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    for i, (ep, g) in enumerate(reg.groupby('endpoint')):
        if ep == '/v1/sensores':
            continue
        p95 = g.set_index('momento')['latencia_ms'].resample('1min').quantile(.95)
        ax.plot(p95.index, p95.values, color=SERIE[i % 3], linewidth=2,
                label=ep)
    if isinstance(ind['latencia_p95_ms'].get('atencao'), (int, float)):
        limiar(ax, ind['latencia_p95_ms']['atencao'],
               f"atenção {ind['latencia_p95_ms']['atencao']:g} ms",
               ESTADO['amarelo'])
        limiar(ax, ind['latencia_p95_ms']['estouro'],
               f"estouro {ind['latencia_p95_ms']['estouro']:g} ms",
               ESTADO['vermelho'])
    ax.set_yscale('log')
    ax.set_ylabel('ms (escala log)')
    ax.set_title('Tempo de resposta, percentil 95 por minuto')
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -.2), ncol=3, fontsize=8)
    eixo_tempo(ax)
    fig.savefig(pasta / 'fig1_latencia.png')
    plt.close(fig)

    # ── figura 2: idade de cada leitura atual ─────────────
    if not atuais.empty:
        fig, ax = plt.subplots(figsize=(7.2, 3.6))
        for i, ((tag, fase), g) in enumerate(atuais.groupby(['tag', 'fase'])):
            ax.scatter(g['momento'], g['idade_dado_s'].clip(lower=.01),
                       s=10, color=SERIE[i % 3], alpha=.6, linewidths=0,
                       label=f'{tag} {fase}')
        for (origem, fase), g in atuais.groupby(['origem', 'fase']):
            lim = g['limite_freshness_s'].iloc[0]
            limiar(ax, lim, f'limite {origem}, {fase}: {lim:g} s',
                   ESTADO['amarelo'])
        ax.set_yscale('log')
        ax.set_ylabel('idade da leitura, s (escala log)')
        ax.set_title('Freshness: idade da leitura em cada chamada de leitura atual')
        ax.legend(loc='upper center', bbox_to_anchor=(.5, -.2), ncol=3,
                  fontsize=8, markerscale=1.6)
        eixo_tempo(ax)
        fig.savefig(pasta / 'fig2_freshness.png')
        plt.close(fig)

        # ── figura 3: completude de freshness por 5 min ───
        fig, ax = plt.subplots(figsize=(7.2, 3.0))
        for i, (tag, g) in enumerate(atuais.groupby('tag')):
            serie = (g.set_index('momento')['dentro_freshness']
                     .resample('5min').mean() * 100).dropna()
            ax.plot(serie.index, serie.values, color=SERIE[i], linewidth=2,
                    marker='o', markersize=4, label=tag)
        regra = ind['completude_freshness_pct']
        limiar(ax, regra['atencao'], f"atenção {regra['atencao']:g}%",
               ESTADO['amarelo'])
        limiar(ax, regra['estouro'], f"estouro {regra['estouro']:g}%",
               ESTADO['vermelho'])
        ax.set_ylim(min(80, ax.get_ylim()[0]), 101)
        ax.set_ylabel('% dentro do limite')
        ax.set_title('Taxa de completude de freshness, a cada 5 minutos')
        ax.legend(loc='upper center', bbox_to_anchor=(.5, -.2), ncol=3,
                  fontsize=8)
        eixo_tempo(ax)
        fig.savefig(pasta / 'fig3_completude_freshness.png')
        plt.close(fig)

        # ── figura 6: severidade ao longo do tempo ────────
        sev = (atuais.set_index('momento')
               .groupby([pd.Grouper(freq='5min'), 'severidade']).size()
               .unstack(fill_value=0))
        sev = sev.div(sev.sum(axis=1), axis=0) * 100
        fig, ax = plt.subplots(figsize=(7.2, 3.0))
        base = pd.Series(0.0, index=sev.index)
        largura = pd.Timedelta(minutes=4)
        for cor in ['verde', 'amarelo', 'vermelho', 'cinza']:
            if cor in sev:
                ax.bar(sev.index, sev[cor], bottom=base, width=largura,
                       color=ESTADO[cor], edgecolor='white', linewidth=1.5,
                       label=ROTULO_COR[cor], align='edge')
                base = base + sev[cor]
        ax.set_ylabel('% das leituras atuais')
        ax.set_title('Cor devolvida pela regra de severidade, a cada 5 minutos')
        ax.legend(loc='upper center', bbox_to_anchor=(.5, -.2), ncol=4,
                  fontsize=8)
        eixo_tempo(ax)
        fig.savefig(pasta / 'fig6_severidade.png')
        plt.close(fig)

    # ── figura 4: completude dos campos por origem ────────
    comp = (reg[reg['completude_pct'].notna()]
            .groupby('origem')['completude_pct'].mean().sort_values())
    if len(comp):
        fig, ax = plt.subplots(figsize=(7.2, 2.2))
        ax.barh(comp.index, comp.values, color=SERIE[0], height=.55)
        for y, v in enumerate(comp.values):
            ax.annotate(f'{v:.0f}%', xy=(v, y), xytext=(4, 0),
                        textcoords='offset points', va='center',
                        color=TEXTO, fontsize=8.5)
        limiar_x = ind['completude_campos_pct']['estouro']
        ax.axvline(limiar_x, color=ESTADO['vermelho'], linestyle='--',
                   linewidth=1)
        ax.set_xlim(0, 110)
        ax.set_xlabel('% dos campos esperados que chegaram')
        ax.set_title('Completude dos campos por origem do dado')
        ax.grid(axis='y', visible=False)
        fig.savefig(pasta / 'fig4_completude_campos.png')
        plt.close(fig)

    # ── figura 5: volume por feature ──────────────────────
    minutos = max((reg['momento'].max() - reg['momento'].min())
                  .total_seconds() / 60, 1)
    vol = (reg['feature'].fillna('(sem feature)').value_counts() / minutos
           ).sort_values()
    fig, ax = plt.subplots(figsize=(7.2, 0.6 + .38 * len(vol)))
    ax.barh(vol.index, vol.values, color=SERIE[0], height=.6)
    for y, v in enumerate(vol.values):
        ax.annotate(f'{v:.1f}', xy=(v, y), xytext=(4, 0),
                    textcoords='offset points', va='center', color=TEXTO,
                    fontsize=8.5)
    ax.set_xlabel('chamadas por minuto')
    ax.set_title('Volume de chamadas por feature (X-Feature)')
    ax.grid(axis='y', visible=False)
    fig.savefig(pasta / 'fig5_volume_feature.png')
    plt.close(fig)

    print(f'  {corpo["total_chamadas"]} chamadas · figuras em {pasta}')
    for chave, i in corpo['indicadores'].items():
        print(f"    {i['situacao']:<13} {chave:<30} {i['valor']}")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--nome', required=True)
    p.add_argument('--de')
    p.add_argument('--ate')
    p.add_argument('--url')
    a = p.parse_args()
    gerar(a.nome, a.de, a.ate, a.url)
