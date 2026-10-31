FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --requirement /tmp/requirements.txt

COPY --chown=app:app data /app/data
COPY --chown=app:app demo /app/demo
COPY --chown=app:app app /app/app
COPY --chown=app:app examples /app/examples

USER 10001:10001

EXPOSE 8000 8501

CMD ["streamlit", "run", "demo/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
