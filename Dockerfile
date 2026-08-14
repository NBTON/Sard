FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    SARD_OUTPUT_ROOT=/home/user/app/output/runs \
    SARD_AUTO_DEMO_FALLBACK=true \
    SARD_DEMO_FALLBACK_TIMEOUT_SECONDS=45

RUN useradd --create-home --uid 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user:user . .
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir ".[nvidia]"

EXPOSE 7860
CMD ["streamlit", "run", "sard/ui/app.py", "--server.address=0.0.0.0", "--server.port=7860"]
