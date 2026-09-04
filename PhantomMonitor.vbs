' Starts PhantomMonitor with no console window.
' Double-click this, or let the tray menu's "Start with Windows" copy it to
' your Startup folder. It finds Python itself, so it works from any location.
Option Explicit

Dim sh, fso, q, appDir, script, pyw, candidates, i

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
q = Chr(34)

appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
script = appDir & "phantommonitor.py"

' If this file was copied elsewhere (the Startup folder, say), fall back to the
' folder it was installed in, recorded on the line below by the tray toggle.
If Not fso.FileExists(script) Then
    script = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\PhantomMonitor\phantommonitor.py"
End If

If Not fso.FileExists(script) Then
    MsgBox "Could not find phantommonitor.py next to this script." & vbCrLf & _
           "Run PhantomMonitor.vbs from the folder you installed it in.", 48, "PhantomMonitor"
    WScript.Quit 1
End If

' pythonw.exe runs without a console window. Try the usual places, then PATH.
candidates = Array( _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python310\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python312\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python311\pythonw.exe")

pyw = ""
For i = 0 To UBound(candidates)
    If pyw = "" And fso.FileExists(candidates(i)) Then
        pyw = candidates(i)
    End If
Next

If pyw = "" Then
    pyw = "pythonw.exe"   ' rely on PATH
End If

sh.Run q & pyw & q & " " & q & script & q, 0, False
