# Process

Analyze by haiku which pulls out the characteristics. These characteristics are
used to improve the prompt like common conventions and to give success criteria
for knowing whether the job was done correctly and to check its work against.

Then this is given to docling for extraction if the PDF isn't a scan or completely
image based. Then the refine loops are actually a single agent that is doing passes looking
for specific things identified by the analyze phase and using the success criteria.

The edits are done with unidiff (https://github.com/matiasb/python-unidiff) against
a file that is in memory.

Another idea is that the refine step could be done by a higher end model and tool
use and/or subagents for the specific edits and the larger agent is overseeing?
The benefit is that the agent writes a prompt for a specific edit and then checks to
see if it applied correctly. The downside is that is it more complex? Something
for this is that I'm letting the model do the work based on the information it
has instead of what I think it has. They're also generally best when allowed to
solve the problem the way they see fit. Perhaps it should be a loop on a higher
end model that is using subagents for edits and it is the thing that decides if
it makes sense or not. The loop should have the markdown lint and spellcheck etc
on it as well to handle the automatic stuff. The intention is to simplify the
orchestration and allow the model to handle the translation from visual into
semantic markup.

I'm not sure about these placeholders. I think instead of the placeholders, we
have it simply converted over and then those are hotspots for touchups because
images,tables, etc are known troublespots for the AI.
