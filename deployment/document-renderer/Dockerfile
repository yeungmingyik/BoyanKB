FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/renderer SAL_USE_VCLPLUGIN=svp
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends libreoffice-writer libreoffice-impress libreoffice-calc fonts-noto-cjk poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY deployment/document-renderer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY branding/templates /app/branding/templates
COPY deployment/document-renderer/render.py deployment/document-renderer/render_pptx_v2.py deployment/document-renderer/server.py /app/
USER 65532:65532
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=3).read()"
CMD ["python", "/app/server.py"]
