-- PROTOTYPE — throwaway. PPTX -> PDF via Keynote (fixes from articulate#21: launch first, no `date:false`).
-- Usage: osascript keynote-export.applescript <in.pptx> <out.pdf>
on run argv
	set inPath to POSIX file (item 1 of argv)
	set outPath to item 2 of argv
	do shell script "touch " & quoted form of outPath
	tell application "Keynote"
		launch
		delay 3
		set doc to open inPath
		export doc to (POSIX file outPath) as PDF with properties {export style:IndividualSlides, all stages:false, skipped slides:true, PDF image quality:Better}
		close doc saving no
	end tell
end run
