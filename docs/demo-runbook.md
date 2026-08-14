# Sard investor demo runbook

## Launch command

From the repository root:

```powershell
uv run streamlit run sard/ui/app.py
```

Open the local URL Streamlit prints. Use the exact hero query:

> أنشئ برنامجًا سياحيًا تراثيًا لمدة يومين في المنطقة الشرقية

The app prefers live execution. For this exact query, it automatically switches to the packaged fallback after `SARD_DEMO_FALLBACK_TIMEOUT_SECONDS` or a failed live terminal result when `SARD_AUTO_DEMO_FALLBACK=true`. The UI always identifies cached content as saved in advance and never as newly generated. The manual **تشغيل النسخة الاحتياطية يدويًا** button is available before a run.

## Setup and readiness gate

```powershell
Copy-Item .env.example .env
# Add NVIDIA_API_KEY to .env for live mode, or leave it blank for fallback-only rehearsal.
uv sync --frozen --all-extras
uv run sard-demo check
uv run sard-demo evaluate --json-out output/evaluation/final-evaluation.json
uv run sard-demo rehearse
```

On a Windows/cloud-synced filesystem that rejects hardlinks, use copy mode:

```powershell
$env:UV_LINK_MODE="copy"
uv sync --frozen --all-extras
```

Interpret `sard-demo check` literally:

- `ready_live`: model access and the complete fallback are ready.
- `ready_fallback_only`: the fallback, corpus, fonts, calendar, PDF, raw text, and output permissions are ready, but live model access is not.
- `not_ready`: do not present until the failing check is fixed.

## Presenter flow

1. Run `uv run sard-demo check` and keep its last screen visible.
2. Start Streamlit and paste/select the hero query.
3. Click **ابدأ التخطيط** to demonstrate live-first behavior.
4. Point out the mode banner. If fallback activates, say plainly: “هذه نسخة محفوظة مسبقًا ومعلّمة بوضوح؛ لم تُنشأ الآن.”
5. Show the cited answer and open two source cards.
6. Download the PDF, `.ics`, and raw-text files.
7. Open the PDF at page 1 and page 3 (sources), then import the `.ics` into a disposable calendar.
8. Stop after the artifact demonstration; do not improvise unsupported current operating details.

## Failure handling

- Live failure or deadline on the exact hero query: allow the automatic fallback to finish; do not refresh while it is switching.
- Manual fallback: click **تشغيل النسخة الاحتياطية يدويًا**.
- A non-hero query fails: use **إعادة المحاولة** or return to the hero query. The packaged cache intentionally refuses other queries.
- `not_ready` self-check: use the backup video and do not claim a live run.
- PDF or calendar check fails: do not distribute that artifact; use the pre-recorded video and investigate after the meeting.

## Final pre-demo checklist

- [ ] `uv run sard-demo check` says `ready_live` or, with explicit presenter approval, `ready_fallback_only`.
- [ ] `uv run sard-demo rehearse` passes with golden score `8/10` or better.
- [ ] Streamlit starts with no traceback and the hero-query buttons work.
- [ ] The live/fallback mode banner is visible and accurate.
- [ ] PDF opens with connected Arabic glyphs, right-to-left paragraphs, source page, and no clipped text.
- [ ] Calendar imports as four events in `Asia/Riyadh`, using a disposable calendar.
- [ ] Raw text opens as UTF-8 Arabic and includes the saved-content warning.
- [ ] Browser zoom is 100%; notifications, password managers, and unrelated tabs are closed.
- [ ] The presenter has the files listed in “Meeting desktop” open.
- [ ] The backup video has been played once with sound off and is available locally.
- [ ] `.env`, `output/`, and `data/zvec/` are ignored by git; `git status --short` shows no private artifacts.

## Record a backup video

1. Set the browser to 1920×1080, 100% zoom, and disable desktop notifications.
2. Run `uv run sard-demo check`; capture the readiness result for three seconds.
3. Start Streamlit, select the hero query, and use the **manual fallback** so the recording is deterministic.
4. Record the visible saved-content banner, progress stages, citations, PDF download/open, calendar download, and raw-text download.
5. Keep the recording under three minutes. Do not edit out the fallback banner.
6. Export MP4/H.264 at 1080p. Name it `sard-investor-backup-2026-08-14.mp4` and store it outside the repository plus one offline copy.
7. Replay the final file on the presentation laptop without network access.

## Deployment

### Streamlit Community Cloud

1. Push the repository to a private or approved GitHub repository.
2. Create a Community Cloud app with entry point `sard/ui/app.py` and Python 3.11.
3. The root `requirements.txt` installs `.[nvidia]`; keep only this dependency declaration for the deployment.
4. In Advanced settings / Secrets, add top-level values matching `.env.example`, especially `NVIDIA_API_KEY`, `SARD_AUTO_DEMO_FALLBACK=true`, `SARD_DEMO_FALLBACK_TIMEOUT_SECONDS=45`, and `SARD_OUTPUT_ROOT=output/runs`.
5. Deploy, open the app, and run the hero query twice: once live and once with the manual fallback.
6. Run the checklist against the deployed URL. Community Cloud copies repository files and runs from the repository root, so the packaged cache and fonts must remain committed. See the [official file-organization guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization) and [secrets guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).

### Hugging Face Spaces (Docker)

1. Create a new Space with SDK **Docker** and set `app_port: 7860` in the Space README YAML.
2. Push this repository, including `Dockerfile`, `requirements.txt`, packaged fonts, and `sard/application/demo_cache/`.
3. Add `NVIDIA_API_KEY` as a Space secret. Add the non-secret `SARD_*` values as Space variables if overriding the Docker defaults.
4. The container runs Streamlit on `0.0.0.0:7860` as UID 1000 and writes generated artifacts under the user-owned app directory.
5. After the Space builds, run the hero query in both live-first and manual-fallback modes. Hugging Face documents Docker Spaces, port 7860, runtime secrets, and UID 1000 in its [official Docker Spaces guide](https://huggingface.co/docs/hub/spaces-sdks-docker).

## Meeting desktop

Have these open before attendees join:

- Browser tab: Sard Streamlit app on the hero query.
- Terminal: latest `uv run sard-demo check` result.
- [Final evaluation](final-evaluation.md).
- This runbook at the presenter flow.
- Packaged PDF: `sard/application/demo_cache/itinerary.pdf`.
- A disposable calendar ready to import `sard/application/demo_cache/itinerary.ics`.
- Local backup video outside the repository.
