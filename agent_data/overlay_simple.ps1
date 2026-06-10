Add-Type -AssemblyName System.Windows.Forms
$o = New-Object Windows.Forms.Form
$o.Text = ''
$o.FormBorderStyle = 'None'
$o.TopMost = $true
$o.StartPosition = 'Manual'
$o.Left = 20
$o.Top = 80
$o.Width = 400
$o.Height = 60
$o.BackColor = 'LightCyan'
$o.Opacity = 0.85
$o.AllowTransparency = $true

$l = New-Object Windows.Forms.Label
$l.Text = 'Ellie: waiting for your keystrokes...  (closes in 7s)'
$l.Font = New-Object Drawing.Font('Yu Gothic', 11)
$l.ForeColor = 'DarkSlateGray'
$l.AutoSize = $false
$l.Width = 380
$l.Height = 40
$l.TextAlign = 'MiddleCenter'
$o.Controls.Add($l)

$t = New-Object Windows.Forms.Timer
$t.Interval = 7000
Register-ObjectEvent -InputObject $t -EventName Tick -Action { $o.Close() } | Out-Null
$t.Start()

[Windows.Forms.Application]::Run($o)
