$ErrorActionPreference = 'Stop'
$demoRecord = Get-Content -LiteralPath 'data/demo/repair-evaluation.json' -Raw | ConvertFrom-Json
$firstCheck = $demoRecord.result.revision_history[0].checks | Where-Object { $_.id -eq 'join_loss' } | Select-Object -First 1
$lastCheck = $demoRecord.result.evidence.checks | Where-Object { $_.id -eq 'join_loss' } | Select-Object -First 1
if ($firstCheck.status -ne 'fail' -or $lastCheck.status -notin @('pass','warn')) { throw 'A completed before-and-after key-retention repair is required.' }
$leftCount = $firstCheck.evidence.left_total
$beforeCount = $firstCheck.evidence.source_keys_retained
$afterCount = $lastCheck.evidence.source_keys_retained
$narration = @"
This is Pipeless, a local prototype for data investigations. Its job is to make the evidence behind a data answer inspectable. A query can run successfully and still leave records out. A left join does not prevent a later filter from dropping them.

This screen is a viewer of recorded live executions. Refreshing it does not call a model. The queries actually ran in Zetaris, and the model responses came through Meterless Nightshift using NVIDIA Nemotron. The demonstration uses Zetaris's sample taxi reference datasets, rather than a production incident.

The repair evaluation clearly labels its starting query. A test harness deliberately supplied a left join with a filter that excludes Location ID one. The initial query is not presented as a spontaneous model mistake. Everything after that input, including the Skeptic's review and the Investigator's repair, uses real model calls and executed data checks.

Here is the important distinction. The reference join found matches for all $leftCount distinct source keys. But the actual query output retained only $beforeCount. The verifier measured the keys in the output, instead of trusting the join keyword. That check failed. The Skeptic cited the failure and requested a revision.

The revised query restores the excluded key. Zetaris then measured $afterCount retained keys out of $leftCount. The revision history keeps the executed SQL and checks, so the change can be inspected. The returned rows provide a review queue for an analyst. The local memory system also records outcomes and the identifiers of lessons retrieved by later work.

There are clear limits. Matching identifiers does not prove that two datasets mean the same thing. Date coverage remains unverified until date values and gaps are checked. This local demonstration uses a builder identity for read-only statements, so it does not prove reader-role isolation. Pipeless focuses on exposing those boundaries alongside the answer.
"@
$narrationPath = Join-Path (Get-Location) 'submission/demo-narration.txt'
[System.IO.File]::WriteAllText($narrationPath, $narration)
Add-Type -AssemblyName System.Speech
$demoSpeaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$demoSpeaker.SelectVoice('Microsoft David Desktop')
$demoSpeaker.Rate = 0
$demoSpeaker.SetOutputToWaveFile((Join-Path (Get-Location) 'submission/demo-narration.wav'))
$demoSpeaker.Speak($narration)
$demoSpeaker.Dispose()
Write-Output 'Synthetic narration generated from completed measured repair evidence.'
