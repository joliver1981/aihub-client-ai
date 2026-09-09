# RU-06c (retest)

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 30.3

## prompt

Do we have a resume on file for Zara Sadeqi? Find ResumeZaraSadeqi.pdf and summarise it.

## tools

- **mcp__aihub__search_documents**
  - input: `{'query': 'Zara Sadeqi resume', 'max_results': 5}`
  - result: ``
- **mcp__aihub__list_documents**
  - input: `{'limit': 50}`
  - result: ``

## reply

I'll search AI Hub's document library for Zara Sadeqi's resume.The search didn't find any documents matching Zara Sadeqi. Let me check what documents you have on file:No — there is no resume for Zara Sadeqi on file in AI Hub's document library. The only documents you have are lease amendment PDFs, not resumes.

**Where the file might be:**
- On the server filesystem directly (outside the document store)
- In a SharePoint or OneDrive integration you haven't imported it from yet
- In a folder somewhere that hasn't been imported

Would you like me to:
1. **Check the server filesystem** for `ResumeZaraSadeqi.pdf` if you know the folder path?
2. **Search an integration** (SharePoint, OneDrive) if it's stored there?
3. **Import a folder** into AI Hub if you tell me where the resume documents live?
