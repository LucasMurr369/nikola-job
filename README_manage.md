# NIKOLA — Live BTNL Margin Bot (Kraken)

## Find It
- **Code:** `C:\Users\mrluk\nikola\nikola.py`
- **Config:** `C:\Users\mrluk\nikola\engine\exchange.py` (nonce fix: `nonce_state.json` persisted)
- **Credentials:** `C:\Users\mrluk\kraken_bot\kraken.env` (shared with HERMES bot)
- **PID file:** `C:\Users\mrluk\nikola\nikola.pid`
- **Log:** `C:\Users\mrluk\nikola\nikola_live.log`

## Quick Status
```bash
cat nikola.pid          # → Windows PID
tail -20 nikola_live.log
tasklist /FI "PID eq $(cat nikola.pid)" /FO TABLE
```

## Start (detached)
From terminal tool:
```
command="C:/Users/mrluk/AppData/Local/Programs/Python/Python312/python.exe -u nikola.py --live --cycles 9999 > nikola_live.log 2>&1"
workdir=C:/Users/mrluk/nikola  background=true  persist_on_release=true
```

## Stop
```bash
taskkill /F /PID $(cat nikola.pid)
```

## Zombie Hunt (find ALL instances)
```bash
# Windows-native — git-bash /proc is INCOMPLETE on this host
tasklist /FI "IMAGENAME eq python.exe" /FO CSV | grep nikola
```
> Git-bash `ps` only sees bash-launched processes. Windows .bat-launched instances are invisible to `/proc`.
> Use `tasklist` + `taskkill` (Windows PIDs), NOT `kill` (git-bash PIDs don't match Windows).
