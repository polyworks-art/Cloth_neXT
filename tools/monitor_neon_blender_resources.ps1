param([Parameter(Mandatory=$true)][string]$Run, [int]$IntervalSeconds=5)
# Read-only sampled resource telemetry for one isolated Blender process tree.
$taskRunPath = (Resolve-Path -LiteralPath $Run).Path
$taskLabel = Split-Path -Leaf $taskRunPath
$taskSamples = [System.Collections.Generic.List[object]]::new()
do {
    $taskProcesses = @(Get-CimInstance Win32_Process)
    $taskIds = @($taskProcesses | Where-Object { $_.Name -eq 'blender.exe' -and $_.CommandLine -like "*$taskLabel*" } | ForEach-Object ProcessId)
    do {
        $taskCount = $taskIds.Count
        $taskIds = @($taskIds + @($taskProcesses | Where-Object { $_.ParentProcessId -in $taskIds } | ForEach-Object ProcessId) | Select-Object -Unique)
    } while ($taskIds.Count -gt $taskCount)
    $taskOwned = @($taskProcesses | Where-Object { $_.ProcessId -in $taskIds })
    $taskGpu = & nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>$null
    $taskGpuFields = "$taskGpu" -split ','
    $taskSamples.Add([ordered]@{
        utc = [DateTime]::UtcNow.ToString('o')
        processes = @($taskOwned | ForEach-Object { [ordered]@{ id=$_.ProcessId; name=$_.Name; rss_bytes=[long]$_.WorkingSetSize; private_bytes=[long]$_.PrivatePageCount } })
        total_rss_bytes = ($taskOwned | Measure-Object -Property WorkingSetSize -Sum).Sum
        total_private_bytes = ($taskOwned | Measure-Object -Property PrivatePageCount -Sum).Sum
        gpu_utilization_percent = if($taskGpuFields.Length -ge 2){$taskGpuFields[0].Trim()}else{$null}
        gpu_global_used_mib = if($taskGpuFields.Length -ge 2){$taskGpuFields[1].Trim()}else{$null}
    })
    $taskSamples | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskRunPath 'resource-samples.json') -Encoding utf8
    try { $taskReport = Get-Content -LiteralPath (Join-Path $taskRunPath 'report.json') -Raw | ConvertFrom-Json } catch { $taskReport=$null }
    if($taskReport -and $taskReport.result -ne 'RUNNING'){break}
    Start-Sleep -Seconds $IntervalSeconds
} while($true)
