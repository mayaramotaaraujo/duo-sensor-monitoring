# ============================================================
#   DUO · SCRIPT DE CONSUMO VIA REQUESTS
#
#   Três jeitos de usar:
#
#   1. Chamar os três endpoints uma vez e ver o que voltou
#        python -m frontend.consumo_requests
#
#   2. Simular um turno de uso normal, para medir a baseline
#        python -m frontend.consumo_requests --simular 30
#      Três pessoas usam o sistema ao mesmo tempo, cada uma com
#      a sua sessão: a sala de controle olhando o BBA-001, o
#      técnico na bancada olhando o REF-001 e o gestor abrindo
#      o painel de observabilidade de vez em quando.
#
#   3. Simular problemas, para ver os limiares estourando
#        python -m frontend.consumo_requests --problemas 5
#      Um programa sem headers de contexto, um técnico digitando
#      o código do motor errado e alguém pedindo o histórico de
#      um dia inteiro sem parar.
# ============================================================

import argparse
import random
import threading
import time

from frontend.cliente_api import ClienteDUO

LINHA = '-' * 66


def mostrar_uma_vez(url):
    cliente = ClienteDUO(url, session_id='script-consumo')
    print('=' * 66)
    print('  DUO · CONSUMO DOS TRÊS ENDPOINTS')
    print('=' * 66)
    print(f'  API: {cliente.url}')
    print(f'  sessão: {cliente.session_id}\n')

    # 1. leitura atual
    corpo, meta = cliente.leitura_atual('BBA-001')
    print(LINHA)
    print('  GET /v1/sensores/BBA-001/leitura-atual')
    print(LINHA)
    if not meta['ok']:
        print(f"  falhou: {meta.get('erro')}")
        return
    sev = corpo['severidade']
    print(f"  {sev['cor'].upper()} · {sev['acao']}")
    print(f"  {sev['causa']}")
    print(f"  fase do motor: {corpo['fase']}")
    print(f"  idade da leitura: {corpo['idade_dado_s']} s "
          f"(limite {corpo['freshness']['limite_s']} s, "
          f"{corpo['freshness']['situacao']})")
    print(f"  completude: {corpo['completude']['preenchidos']} de "
          f"{corpo['completude']['esperados']} campos")
    for campo, valor in corpo['leitura'].items():
        print(f"    {campo:<16} {valor} {corpo['unidades'][campo]}")
    print(f"  request_id {meta['request_id']} · "
          f"{meta['latencia_api_ms']:.1f} ms na API · "
          f"{meta['latencia_front_ms']:.1f} ms até aqui")

    # 2. histórico
    corpo, meta = cliente.historico('BBA-001', minutos=30)
    print(f'\n{LINHA}')
    print('  GET /v1/sensores/BBA-001/historico?minutos=30')
    print(LINHA)
    print(f"  {corpo['quantidade']} leituras na janela, completude média "
          f"{corpo['completude_media_pct']}%")
    if corpo['leituras']:
        ultima = corpo['leituras'][-1]
        print(f"  última: {ultima['momento']} · vibração "
              f"{ultima['velocidade_1']} mm/s · {ultima['fase']}")
    print(f"  request_id {meta['request_id']} · "
          f"{meta['latencia_api_ms']:.1f} ms na API")

    # 3. observabilidade
    corpo, meta = cliente.observabilidade(limite=5)
    print(f'\n{LINHA}')
    print('  GET /v1/observabilidade')
    print(LINHA)
    print(f"  {corpo['total_chamadas']} chamadas registradas\n")
    for ind in corpo['indicadores'].values():
        valor = 'sem dado' if ind['valor'] is None else ind['valor']
        print(f"  {ind['situacao']:<13} {ind['nome'][:42]:<44} "
              f"{valor} {ind['unidade']}")
    print('\n  últimos registros:')
    for r in corpo['registros']:
        print(f"    {r['momento']}  {r['feature'] or '(sem feature)':<22} "
              f"{r['status']}  {r['latencia_ms']:.1f} ms  {r['caminho']}")


# ══════════════════════════════════════════════════════════════
#  SIMULAÇÃO DE USO
# ══════════════════════════════════════════════════════════════

def _repetir(parar, intervalo, tarefa, variacao=0.2):
    while not parar.is_set():
        try:
            tarefa()
        except Exception as e:
            print(f'  falha numa chamada: {e}')
        espera = intervalo * random.uniform(1 - variacao, 1 + variacao)
        parar.wait(espera)


def simular_uso_normal(url, minutos):
    sala = ClienteDUO(url, session_id='sala-de-controle')
    tecnico = ClienteDUO(url, session_id='tecnico-bancada')
    gestor = ClienteDUO(url, session_id='gestor-manutencao')

    tarefas = [
        # a tela da sala de controle atualiza a leitura a cada 3 s
        (3, lambda: sala.leitura_atual('BBA-001')),
        (60, lambda: sala.historico('BBA-001', minutos=30)),
        # o técnico acompanha o motor de referência
        (5, lambda: tecnico.leitura_atual('REF-001')),
        (45, lambda: tecnico.historico('REF-001', minutos=15)),
        (180, lambda: tecnico.historico('BBA-001', fonte='gravado',
                                        de='2026-08-01')),
        # o gestor abre o painel e confere o motor sem sensor
        (90, lambda: gestor.observabilidade(limite=50)),
        (120, lambda: gestor.leitura_atual('BBA-002')),
    ]
    _rodar(tarefas, minutos, 'uso normal')


def simular_problemas(url, minutos):
    sem_contexto = ClienteDUO(url, enviar_contexto=False)
    distraido = ClienteDUO(url, session_id='tecnico-digitando')
    pesado = ClienteDUO(url, session_id='relatorio-pesado')
    sala = ClienteDUO(url, session_id='sala-de-controle')

    tarefas = [
        (3, lambda: sala.leitura_atual('BBA-001')),
        (4, lambda: sem_contexto.leitura_atual('BBA-001')),
        (6, lambda: distraido.leitura_atual(random.choice(
            ['BBA001', 'bba-001', 'BBA-01', 'REF-001']))),
        (5, lambda: pesado.historico('BBA-001', minutos=24 * 60,
                                     limite=10000)),
    ]
    _rodar(tarefas, minutos, 'problemas')


def _rodar(tarefas, minutos, nome):
    print(f'  simulando {nome} por {minutos} min '
          f'({len(tarefas)} rotinas em paralelo)')
    parar = threading.Event()
    fios = [threading.Thread(target=_repetir, args=(parar, i, t), daemon=True)
            for i, t in tarefas]
    for f in fios:
        f.start()
    fim = time.time() + minutos * 60
    try:
        while time.time() < fim:
            restante = (fim - time.time()) / 60
            print(f'  ... faltam {restante:.1f} min', flush=True)
            time.sleep(min(60, max(fim - time.time(), 0)))
    except KeyboardInterrupt:
        print('  interrompido')
    parar.set()
    for f in fios:
        f.join(timeout=20)
    print('  simulação encerrada')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Consumo da API do DUO')
    p.add_argument('--url', default=None, help='endereço da API')
    p.add_argument('--simular', type=float, metavar='MIN',
                   help='simula uso normal por MIN minutos')
    p.add_argument('--problemas', type=float, metavar='MIN',
                   help='simula problemas por MIN minutos')
    a = p.parse_args()

    if a.simular:
        simular_uso_normal(a.url, a.simular)
    elif a.problemas:
        simular_problemas(a.url, a.problemas)
    else:
        mostrar_uma_vez(a.url)
