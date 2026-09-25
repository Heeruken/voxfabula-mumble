# Immagine del relay di NWN Voce. Si costruisce dalla RADICE del progetto:
#     docker build -f relay/Dockerfile -t nwn-voce-relay .
# Il .dockerignore della radice lascia passare solo relay/ e i 3 file del
# protocollo condiviso: la build manda a Docker pochi KB, non l'intero disco.

# Python 3.11 (non 3.12): pymumble 1.6.1 usa ssl.wrap_socket(), rimosso nella 3.12.
FROM python:3.11-slim-bookworm

# libopus0: pymumble/opuslib la trovano via ctypes.util.find_library("opus").
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# prima le dipendenze (strato in cache finche' requirements.txt non cambia)
COPY relay/requirements.txt /app/relay/requirements.txt
RUN pip install --no-cache-dir -r /app/relay/requirements.txt

# protocollo condiviso con l'app + codice del relay
COPY nwn_voce/__init__.py nwn_voce/net_protocol.py nwn_voce/state.py /app/nwn_voce/
COPY relay/*.py /app/relay/

ENTRYPOINT ["python", "-m", "relay"]
