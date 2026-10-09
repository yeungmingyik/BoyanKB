#requires -Version 7.0
param(
    [Parameter(Mandatory)][string[]]$Report,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$artifacts = [Collections.Generic.List[object]]::new()
foreach ($reportPath in $Report) {
    $absoluteReport = (Resolve-Path -LiteralPath $reportPath).Path
    $data = Get-Content -LiteralPath $absoluteReport -Raw | ConvertFrom-Json
    foreach ($artifact in $data.artifacts) {
        $file = [IO.Path]::GetFullPath((Join-Path (Split-Path $absoluteReport) $artifact.path))
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifact.sha256) {
            throw "Artifact digest mismatch: $file"
        }
        $artifacts.Add([pscustomobject]@{File=$file;Format=$artifact.format;Job=$artifact.job_id;SHA256=$artifact.sha256})
    }
}
if ($artifacts.Count -eq 0) { throw 'No published artifacts found' }
$processNames = @($artifacts.Format | Select-Object -Unique | ForEach-Object { @{pptx='POWERPNT';docx='WINWORD';xlsx='EXCEL'}[$_] } | Where-Object { $_ })
if ($processNames.Count -gt 0 -and @(Get-Process -Name $processNames -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Close Word, Excel and PowerPoint after saving your work, then run this verification again'
}
$results = [Collections.Generic.List[object]]::new()
foreach ($format in @('pptx','docx','xlsx')) {
    $files = @($artifacts | Where-Object Format -eq $format)
    if ($files.Count -eq 0) { continue }
    $program = @{pptx='PowerPoint.Application';docx='Word.Application';xlsx='Excel.Application'}[$format]
    $processName = @{pptx='POWERPNT';docx='WINWORD';xlsx='EXCEL'}[$format]
    if (@(Get-Process $processName -ErrorAction SilentlyContinue).Count -gt 0) { throw "Office application already running: $processName" }
    $application = New-Object -ComObject $program
    try {
        $application.AutomationSecurity = 3
        if ($format -in @('docx','xlsx')) {
            $application.Visible = $false
        }
        if ($format -eq 'docx') { $application.DisplayAlerts = 0 }
        if ($format -eq 'xlsx') { $application.DisplayAlerts = $false }
        foreach ($artifact in $files) {
            $destination = Join-Path $outputRoot ($artifact.Job+'-'+$format)
            New-Item -ItemType Directory -Force -Path $destination | Out-Null
            $entry = @{job_id=$artifact.Job;format=$format;sha256=$artifact.SHA256;opened=$false;preview=$destination}
            if ($format -eq 'pptx') {
                $document = $application.Presentations.Open($artifact.File, -1, 0, 0)
                try {
                    $document.Export($destination, 'PNG', 1280, 720)
                    $entry.slides = $document.Slides.Count
                    $overflow = [Collections.Generic.List[object]]::new()
                    foreach ($slide in $document.Slides) {
                        foreach ($shape in $slide.Shapes) {
                            if ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
                                $frame = $shape.TextFrame
                                if ($frame.TextRange.BoundHeight -gt ($shape.Height-$frame.MarginTop-$frame.MarginBottom+2)) {
                                    $overflow.Add(@{slide=$slide.SlideIndex;shape=$shape.Name})
                                }
                            }
                        }
                    }
                    $entry.text_overflow = @($overflow)
                    $entry.opened = $true
                } finally { $document.Close() }
            } elseif ($format -eq 'docx') {
                $document = $application.Documents.Open($artifact.File, $false, $true, $false)
                try {
                    $document.ExportAsFixedFormat((Join-Path $destination 'document.pdf'), 17)
                    $entry.pages = $document.ComputeStatistics(2)
                    $entry.opened = $true
                } finally { $document.Close(0) }
            } else {
                $document = $application.Workbooks.Open($artifact.File, 0, $true)
                try {
                    $before = @{}
                    foreach ($sheet in $document.Worksheets) {
                        foreach ($cell in $sheet.UsedRange.Cells) {
                            if ($cell.HasFormula) { $before[$sheet.Name+'!'+$cell.Address()] = $cell.Value2 }
                        }
                    }
                    $application.CalculateFullRebuild()
                    $formulas = [Collections.Generic.List[object]]::new()
                    foreach ($sheet in $document.Worksheets) {
                        foreach ($cell in $sheet.UsedRange.Cells) {
                            if ($cell.HasFormula) {
                                $key = $sheet.Name+'!'+$cell.Address()
                                if ($cell.Text -match '^#(REF!|VALUE!|DIV/0!|NAME\?|NUM!|N/A|SPILL!)') { throw "Excel formula error: $key" }
                                if ($cell.Value2 -ne $before[$key]) { throw "Excel recalculation mismatch: $key" }
                                $formulas.Add(@{cell=$key;formula=$cell.Formula;value=$cell.Value2})
                            }
                        }
                    }
                    $dynamicChecks = [Collections.Generic.List[object]]::new()
                    foreach ($sheet in $document.Worksheets) {
                        foreach ($cell in $sheet.UsedRange.Cells) {
                            if (-not $cell.HasFormula -or $cell.Formula -notmatch '^=ROUND\(B(\d+)\*C\1,2\)$') { continue }
                            $budgetRow = [int]$Matches[1]
                            $budgetTotal = $null
                            foreach ($candidate in $sheet.UsedRange.Cells) {
                                if ($candidate.HasFormula -and $candidate.Formula -match '^=SUM\(D(\d+):D(\d+)\)$' -and $budgetRow -ge [int]$Matches[1] -and $budgetRow -le [int]$Matches[2]) {
                                    $budgetTotal = $candidate
                                    break
                                }
                            }
                            if ($null -eq $budgetTotal) { throw "Budget total formula missing: $($sheet.Name)!D$budgetRow" }
                            foreach ($inputColumn in @('B','C')) {
                                $inputCell = $sheet.Range($inputColumn+$budgetRow)
                                $originalInput = $inputCell.Value2
                                $originalAmount = $cell.Value2
                                $originalTotal = $budgetTotal.Value2
                                try {
                                    $inputCell.Value2 = [double]$originalInput + $(if ($inputColumn -eq 'B') { 1.0 } else { 0.01 })
                                    $application.CalculateFullRebuild()
                                    $expectedAmount = $application.WorksheetFunction.Round($sheet.Range('B'+$budgetRow).Value2 * $sheet.Range('C'+$budgetRow).Value2, 2)
                                    $expectedTotal = $originalTotal - $originalAmount + $expectedAmount
                                    if ([Math]::Abs($cell.Value2-$expectedAmount) -gt 0.0000001 -or [Math]::Abs($budgetTotal.Value2-$expectedTotal) -gt 0.0000001) {
                                        throw "Budget does not recalculate after editing $($sheet.Name)!$inputColumn$budgetRow"
                                    }
                                    $dynamicChecks.Add(@{input=$sheet.Name+'!'+$inputCell.Address();amount=$cell.Address();total=$budgetTotal.Address();passed=$true})
                                } finally {
                                    $inputCell.Value2 = $originalInput
                                    $application.CalculateFullRebuild()
                                }
                                if ($cell.Value2 -ne $originalAmount -or $budgetTotal.Value2 -ne $originalTotal) { throw 'Budget input restoration mismatch' }
                            }
                        }
                    }
                    $document.ExportAsFixedFormat(0, (Join-Path $destination 'workbook.pdf'))
                    $entry.formulas = @($formulas)
                    $entry.dynamic_budget_checks = @($dynamicChecks)
                    $entry.sheets = $document.Worksheets.Count
                    $entry.opened = $true
                } finally { $document.Close($false) }
            }
            if ((Get-FileHash -LiteralPath $artifact.File -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifact.SHA256) { throw 'Verification changed the original artifact' }
            $results.Add($entry)
        }
    } finally {
        $application.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($application)
    }
}
foreach ($artifact in @($artifacts | Where-Object Format -eq 'md')) {
    $content = Get-Content -LiteralPath $artifact.File -Raw -Encoding utf8
    if (-not $content.Contains('博研文化') -or -not $content.Contains('资料来源')) { throw 'Markdown brand or sources missing' }
    $results.Add(@{job_id=$artifact.Job;format='md';sha256=$artifact.SHA256;opened=$true;utf8=$true})
}
$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputRoot 'native-office.json') -Encoding utf8
$results | ConvertTo-Json -Depth 8 -Compress
if (@($results | Where-Object { $_.text_overflow.Count -gt 0 }).Count -gt 0) { exit 1 }
