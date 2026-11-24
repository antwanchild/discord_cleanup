# Discord embed fields do not render Markdown headings.
def discord_notes:
  gsub("\r"; "")
  | split("\n")
  | map(
      if test("^#{1,6}\\s+Changes\\s*$"; "i") then empty
      elif test("^#{1,2}\\s+") then empty
      elif test("^#{3,6}\\s+") then
        "**" + sub("^#{3,6}\\s+"; "") + "**"
      elif test("^[-*]\\s+") then sub("^[-*]\\s+"; "• ")
      else . end
    )
  | join("\n")
  | gsub("\n{3,}"; "\n\n")
  | sub("^\\s+"; "") | sub("\\s+$"; "")
  | if length <= 1024 then . else
      split("\n")
      | reduce .[] as $line
          ({text: "", stopped: false};
           if .stopped then .
           elif ((.text + "\n" + $line) | length) <= 980 then
             .text += (if .text == "" then "" else "\n" end) + $line
           else .stopped = true end)
      | .text + "\n… See the full release notes."
    end;
