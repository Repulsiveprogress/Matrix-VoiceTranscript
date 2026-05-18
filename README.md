# Matrix Voice Transcript

> **Transcription languages / Языки распознавания / Sprachen / Langues:**
> 🇬🇧 English · 🇷🇺 Русский · 🇩🇪 Deutsch · 🇫🇷 Français · 🇪🇸 Español · 🇵🇹 Português · 🇵🇱 Polski · 🇨🇿 Czech · 🇸🇰 Slovak · 🇧🇬 Bulgarian · 🇺🇦 Ukrainian · 🇳🇱 Dutch · 🇮🇹 Italian · 🇷🇴 Romanian · 🇸🇪 Swedish · 🇩🇰 Danish · 🇫🇮 Finnish · 🇳🇴 Norwegian · 🇬🇷 Greek · 🇭🇺 Hungarian · 🇪🇪 Estonian · 🇱🇻 Latvian · 🇱🇹 Lithuanian · 🇸🇮 Slovenian · 🇭🇷 Croatian · 🇲🇹 Maltese

Matrix bot that transcribes voice messages and audio files using [NVIDIA NeMo Parakeet TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) running locally on CPU. Supports E2EE rooms. No audio leaves the server.

## Requirements

- Matrix bot account with an access token.
- `MATRIX_PASSWORD` recommended for E2EE rooms (enables stale-device pruning; without it decryption may fail on first run).
- ~2.5 GB disk space for the model checkpoint (cached in `./models`, downloaded on first start).

## Quick start

1. Copy `.env.example` to `.env` and fill in the variables.
2. `docker compose up -d`
3. Invite the bot to a Matrix room.

## Environment variables

| Variable | Description |
|---|---|
| `MATRIX_HS_URL` | Homeserver URL (with `https://`) |
| `MATRIX_USER_ID` | Full bot MXID, e.g. `@voicebot:example.org` |
| `MATRIX_ACCESS_TOKEN` | Bot access token |
| `MATRIX_PASSWORD` | Optional. Prunes stale E2EE devices on startup; required for reliable decryption in encrypted rooms. |
| `LOCALE` | Message language: `en` (default) or `ru` |
| `ASR_MODEL_NAME` | NeMo model (default: `nvidia/parakeet-tdt-0.6b-v3`) |
| `MAX_AUDIO_BYTES` | Max file size in bytes (default: `26214400` = 25 MB) |
| `STORE_PATH` | Olm key store path inside the container (default: `/data/store`) |

**Supported formats:** ogg/opus, webm, mp4/m4a, aac, flac, mp3, wav.

## Message language / Смена языка

```env
LOCALE=en   # English (default)
LOCALE=ru   # Russian / Русский
```

`docker compose restart` to apply.

## Local development

Requires Python 3.11+ and `ffmpeg` on PATH.

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
python -m src.main
```

## Security

- Never commit `.env`.
- Transcribed text is never written to logs.
- Temp audio files are deleted immediately after transcription.

## License

MIT
