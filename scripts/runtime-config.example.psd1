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
    # Optional (Voz 3): Whisper on the GPU. Defaults: 'cpu', 'int8' ('int8_float16' on cuda), no fallback,
    # $false, 1000 and 900. WhisperModel then points to faster-whisper-large-v3-turbo.
    # WhisperDevice = 'cuda'                # 'cpu' or 'cuda'
    # WhisperComputeType = 'int8_float16'   # cuda: 'int8_float16', 'float16', 'int8'; cpu: 'int8', 'int8_float32', 'float32'
    # WhisperFallbackModel = ''             # absolute folder of faster-whisper-small, used on the CPU
    # WhisperPreload = $false
    # WhisperMinVramMiB = 1000
    # WhisperIdleSeconds = 900
    # Optional (users and roles, docs/users.md). Defaults: 5, 'chat' and 'mobile'.
    # PasswordMinLength = 5          # 5 to 256; raise it before publishing the web to other PCs
    # LoopbackMode = 'chat'          # this PC without a login: 'legacy' (everything, rollback), 'chat' or 'off'
    # BearerScope = 'mobile'         # shared tokens (app v1): 'mobile' (phone routes) or 'full' (rollback)
}
