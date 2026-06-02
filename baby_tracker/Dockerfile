ARG BUILD_FROM
FROM ${BUILD_FROM}

# The HA base-python images already ship Python 3.12 + a tini/s6 init.
# bashio is provided by the base image for reading /data/options.json.

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Application code: the `app` package + the static SPA in `web`.
# main.py resolves WEB_DIR as <parent-of-app>/web, so `web` lives next to `app`.
COPY app/ /app/app/
COPY web/ /app/web/

# Add-on entrypoint.
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
