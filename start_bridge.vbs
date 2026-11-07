' Starts the MiniLab 3 <-> MorningDAW bridge in the background (no console window).
' It waits for the MiniLab and the loopMIDI ports, so it can run at Windows login.
' Uses "pyw" (the Python launcher, windowless) from PATH. Stop it with stop_bridge.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = here
sh.Run "pyw """ & here & "\minilab3_bridge.py"" --wait", 0, False
