FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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

# Supported deployment: FastAPI backend (Next.js frontend deploys separately,
# e.g. Vercel — see vercel.json). The legacy Streamlit demo entrypoint was
# removed; use `npm run build && npm run start` for the web UI.
EXPOSE 8000
CMD ["uvicorn", "sard.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
