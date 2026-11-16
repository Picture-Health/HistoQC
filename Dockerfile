# Dockerfile for the Picture Health HistoQC image (TRD-3554).
#
# The image reproduces the environment the TRD-3518 golden chains were made in
# (tests/golden/README.md): Python 3.10.20 and the exact package versions in
# requirements-image.lock, installed hash-checked from wheels only.
#
# pathology-slide-reader is a private GitHub repo, so the build needs a GitHub
# token with read access to Picture-Health/pathology-slide-reader, passed as a
# BuildKit secret (never a build arg, so it stays out of every layer):
#
#   GITHUB_TOKEN=$(gh auth token) docker build --platform linux/amd64 \
#       --secret id=github_token,env=GITHUB_TOKEN -t histoqc .

FROM python:3.10.20-slim AS builder
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/HistoQC/venv
ENV PATH="/opt/HistoQC/venv/bin:$PATH"
WORKDIR /opt/HistoQC

# Exactly the lock, hash-checked, prebuilt wheels only: `pip install -r
# requirements.txt` resolves version ranges at build time, so two builds of one
# commit could ship different numpy / scikit-learn / OpenCV and write different
# masks.
COPY requirements-image.lock ./
RUN pip install --no-cache-dir --require-hashes --only-binary :all: \
        -r requirements-image.lock

# The private reader, at the commit pinned in requirements.txt, with no
# dependency resolution (its dependencies are in the lock). The token only
# exists in this RUN's environment: git reads the URL rewrite from GIT_CONFIG_*
# variables, so no config file or URL holding it is written to the layer.
COPY requirements.txt ./
RUN --mount=type=secret,id=github_token,required=true \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/github_token)@github.com/.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/" \
    pip install --no-cache-dir --no-deps \
        "$(grep '^pathology-slide-reader @' requirements.txt)" \
    && pip check

# HistoQC runs from source on PYTHONPATH, as the goldens were made (`python -m
# histoqc` from the repo root), with the PH production config alongside.
COPY histoqc ./histoqc
COPY tests/picture_health/config_ph_v2.1.ini ./config/config_ph_v2.1.ini

FROM python:3.10.20-slim
COPY --from=builder /opt/HistoQC /opt/HistoQC
ENV PATH="/opt/HistoQC/venv/bin:$PATH" \
    PYTHONPATH=/opt/HistoQC \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg
RUN groupadd --gid 1000 histoqc \
    && useradd --uid 1000 --gid histoqc --create-home histoqc \
    # HistoQC unpacks its package data into a temp dir under the cwd.
    && mkdir /data && chown histoqc:histoqc /data
USER histoqc
WORKDIR /data

# docker run -v "$PWD":/data histoqc -c /opt/HistoQC/config/config_ph_v2.1.ini \
#     -o out -n 1 slide_std.tiff
ENTRYPOINT ["python", "-m", "histoqc"]
CMD ["--help"]
