$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_OperatingSystem | Select-Object @{Name='TotalRAMGiB';Expression={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}},@{Name='FreeRAMGiB';Expression={[math]::Round($_.FreePhysicalMemory/1MB,2)}}
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv
Get-PSDrive C,D | Select-Object Name,@{Name='FreeGiB';Expression={[math]::Round($_.Free/1GB,2)}}
