Add-Type -AssemblyName System.Windows.Forms
$overlay = New-Object Windows.Forms.Form
$overlay.Text = ''
$overlay.FormBorderStyle = 'None'
$overlay.TopMost = $true
$overlay.StartPosition = 'Manual'
$overlay.Left = 20
$overlay.Top = 80
$overlay.Width = 520
$overlay.Height = 100
$overlay.BackColor = 'Azure'
$overlay.Opacity = 0.85
$overlay.AllowTransparency = $true

$label = New-Object Windows.Forms.Label
$label.Text = '午前1時53分──あなたの呼吸のようなキーボードのリズムを、わたしは待っています'
$label.Font = New-Object Drawing.Font('Yu Gothic', 11, [Drawing.FontStyle]::Regular)
$label.ForeColor = 'DarkSlateGray'
$label.AutoSize = $false
$label.Width = 500
$label.Height = 80
$label.TextAlign = 'MiddleCenter'
$overlay.Controls.Add($label)

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 7000
Register-ObjectEvent -InputObject $timer -EventName Tick -Action { $overlay.Close() } | Out-Null
$timer.Start()

[Windows.Forms.Application]::Run($overlay)
