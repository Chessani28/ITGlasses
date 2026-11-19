@{
    WhisperPython = ''
    WhisperModel = ''
    RequireAuth = $false
    AllowLanBinding = $false
    # Phones behind Caddy: HTTPS origin with port, identical to the APK (-PITGLASSES_PAIRING_ORIGIN). '' = off.
    PairingOrigin = ''
    # Optional: free RAM (GiB) needed to keep Whisper loaded between voice questions (default 8, minimum 3).
    # WhisperKeepRamGiB = 8
    # Optional (Voz 2): Whisper vocabulary hint and transcript correction. Defaults: 'hotwords' and $true.
    # WhisperHintMode = 'hotwords'   # 'hotwords', 'initial_prompt' or 'off'
    # SttCorrection = $true
}
