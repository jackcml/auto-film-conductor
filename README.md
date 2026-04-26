# Auto Film Conductor

Automates an internal movie-night flow:

1. collect chat suggestions,
2. validate and dedupe films,
3. randomly sample a poll slate,
4. run approval voting and ranked-choice runoff,
5. hand the winner to Radarr,
6. load the imported file into local `mpv`.

The first implementation is Discord-first but adapter-based. The voting platform is a mock HTTP API for now so real poll providers can be added without rewriting conductor logic.

## Run Locally

```powershell
uv sync --extra dev
uv run uvicorn auto_film_conductor.app:app --reload
```

Run the Discord adapter in a second process after setting `AFC_DISCORD_TOKEN`:

```powershell
uv run afc-discord
```

## Core Settings

Settings are read from environment variables.

| Variable                        | Default                              | Purpose                                  |
| ------------------------------- | ------------------------------------ | ---------------------------------------- |
| `AFC_DATABASE_URL`              | `sqlite:///./auto-film-conductor.db` | SQLite database URL                      |
| `AFC_SUGGESTION_WINDOW_SECONDS` | `300`                                | Collection window duration               |
| `AFC_SAMPLE_SIZE`               | `15`                                 | Maximum films selected for approval poll |
| `AFC_RUNOFF_SIZE`               | `5`                                  | Maximum films selected for RCV runoff    |
| `AFC_APPROVAL_POLL_SECONDS`     | `300`                                | Approval voting duration                 |
| `AFC_RCV_POLL_SECONDS`          | `300`                                | Ranked-choice voting duration            |
| `AFC_DISCORD_TOKEN`             | empty                                | Discord bot token                        |
| `AFC_DISCORD_GUILD_ID`          | empty                                | Optional guild ID for command sync       |
| `AFC_DISCORD_CHANNEL_ID`        | empty                                | Channel where mentions are accepted      |
| `AFC_DISCORD_ADMIN_ROLE_ID`     | empty                                | Role allowed to use conductor controls   |
| `AFC_RADARR_URL`                | empty                                | Radarr base URL                          |
| `AFC_RADARR_API_KEY`            | empty                                | Radarr API key                           |
| `AFC_RADARR_ROOT_FOLDER_PATH`   | empty                                | Root folder for new movies               |
| `AFC_RADARR_QUALITY_PROFILE_ID` | `1`                                  | Radarr quality profile ID                |
| `AFC_MPV_IPC_PATH`              | `\\.\pipe\mpv-pipe`                  | mpv IPC pipe/socket path                 |

## Useful Endpoints

```text
GET  /health
GET  /rounds/current
POST /rounds/start
POST /rounds/{round_id}/suggestions
POST /rounds/{round_id}/close-collection
POST /rounds/{round_id}/close-approval
POST /rounds/{round_id}/close-rcv
POST /rounds/{round_id}/pause
POST /rounds/{round_id}/resume
POST /rounds/{round_id}/cancel
POST /rounds/{round_id}/reroll
POST /rounds/{round_id}/override
POST /playback/stop
```

Mock poll API:

```text
POST /mock-polls
POST /mock-polls/{poll_id}/votes
POST /mock-polls/{poll_id}/close
GET  /mock-polls/{poll_id}/results
```

Discord operator commands are grouped under `/conductor`: `start`, `status`, `pause`, `resume`, `cancel`, `force_close`, `reroll`, `override`, and `stop`.
