-- Usage: osascript keynote-export.applescript <in.pptx> <out.pdf>   (create out.pdf first)
on run argv
	set src to POSIX file (item 1 of argv)
	set dst to POSIX file (item 2 of argv)
	tell application "Keynote" to launch
	repeat 120 times
		try
			tell application "Keynote" to count of documents
			exit repeat
		on error
			delay 0.25
		end try
	end repeat
	with timeout of 600 seconds
		tell application "Keynote"
			set d to open src
			try
				export d to dst as PDF with properties {export style:IndividualSlides, all stages:false, skipped slides:true, slide numbers:false, PDF image quality:Better}
				set n to count of slides of d
				close d saving no
			on error e number k
				close d saving no
				error e number k
			end try
		end tell
	end timeout
	return (n as text)
end run
