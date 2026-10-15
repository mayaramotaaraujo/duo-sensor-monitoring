# ============================================================
#   DUO · CLIENTE DA API
#
#   Toda chamada do front passa por aqui. É este arquivo que
#   garante que os headers de contexto (X-Session-Id e
#   X-Feature) vão em todas as chamadas, tanto da tela quanto
#   do script de consumo.
# ============================================================

import os
import time
import uuid

import requests

URL_PADRAO = os.environ.get('DUO_API_URL', 'http://127.0.0.1:8000')


class ClienteDUO:
    def __init__(self, url=None, session_id=None, enviar_contexto=True,
                 tempo_limite=15):
        self.url = (url or URL_PADRAO).rstrip('/')
        self.session_id = session_id or f'sessao-{uuid.uuid4().hex[:10]}'
        self.enviar_contexto = enviar_contexto
        self.tempo_limite = tempo_limite
        self.http = requests.Session()

    def _chamar(self, caminho, feature, parametros=None):
        """Faz o GET e devolve a resposta junto com o que o próprio
           front mediu: status, tempo e o request_id da API."""
        headers = {}
        if self.enviar_contexto:
            headers = {'X-Session-Id': self.session_id,
                       'X-Feature': feature}
        inicio = time.perf_counter()
        try:
            r = self.http.get(f'{self.url}{caminho}', params=parametros,
                              headers=headers, timeout=self.tempo_limite)
        except requests.RequestException as e:
            return None, {'ok': False, 'status': None,
                          'erro': f'a API não respondeu: {e.__class__.__name__}',
                          'latencia_front_ms': round(
                              (time.perf_counter() - inicio) * 1000, 1)}
        meta = {
            'ok': r.ok, 'status': r.status_code,
            'request_id': r.headers.get('X-Request-Id'),
            'latencia_api_ms': float(r.headers.get('X-Latencia-ms', 0) or 0),
            'latencia_front_ms': round((time.perf_counter() - inicio) * 1000, 1),
        }
        try:
            corpo = r.json()
        except ValueError:
            corpo = None
        if not r.ok:
            meta['erro'] = (corpo or {}).get('detail') or \
                (corpo or {}).get('erro') or r.text[:200]
        return corpo, meta

    # ── os três endpoints do checkpoint ──────────────────────
    def leitura_atual(self, tag, base='rigida', feature='leitura_atual'):
        return self._chamar(f'/v1/sensores/{tag}/leitura-atual', feature,
                            {'base': base})

    def historico(self, tag, minutos=30, fonte='ao_vivo', limite=1500,
                  base='rigida', de=None, ate=None, feature='historico'):
        parametros = {'minutos': minutos, 'fonte': fonte, 'limite': limite,
                      'base': base}
        if de:
            parametros['de'] = de
        if ate:
            parametros['ate'] = ate
        return self._chamar(f'/v1/sensores/{tag}/historico', feature,
                            parametros)

    def observabilidade(self, limite=300, minutos=None, feature_filtro=None,
                        session_filtro=None, incluir_painel=False,
                        de=None, ate=None, feature='painel_observabilidade'):
        parametros = {'limite': limite, 'incluir_painel': incluir_painel}
        if de:
            parametros['de'] = de
        if ate:
            parametros['ate'] = ate
        if minutos:
            parametros['minutos'] = minutos
        if feature_filtro:
            parametros['feature'] = feature_filtro
        if session_filtro:
            parametros['session_id'] = session_filtro
        return self._chamar('/v1/observabilidade', feature, parametros)

    # ── apoio ────────────────────────────────────────────────
    def motores(self, feature='lista_motores'):
        return self._chamar('/v1/sensores', feature)

    def contrato(self, feature='painel_observabilidade'):
        return self._chamar('/v1/observabilidade/contrato', feature)
