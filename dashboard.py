import streamlit as st
import PyPDF2
from src.agents.hr_input_agent import HrInputAgent
from src.agents.search_agent import SearchAgent
from src.agents.resume_parser_agent import ResumeParserAgent
from src.agents.ranking_agent import RankingAgent
from src.tools.internal_tool import InternalTool
from src.tools.llm_tool import parse_resume_with_llm, semantic_rank, extract_resume_info

st.title("HR Resume Ranking System")

# Step 1: Upload Job Description
st.header("1. Job Description")
job_title = st.text_input("Job Title", "Software Engineer")
required_skills = st.text_area("Required Skills (comma-separated)", "Python, APIs, SQL")
job_description = {
    "title": job_title,
    "required_skills": [skill.strip() for skill in required_skills.split(",") if skill.strip()]
}

# Step 2: Upload Resumes (PDFs)
st.header("2. Upload Resumes (PDFs)")
uploaded_files = st.file_uploader("Upload multiple PDF resumes", type=["pdf"], accept_multiple_files=True)

resumes = []
for uploaded_file in uploaded_files:
    pdf_reader = PyPDF2.PdfReader(uploaded_file)
    content = ""
    for page in pdf_reader.pages:
        content += page.extract_text() or ""
    if content:
        resumes.append({
            "file_name": uploaded_file.name,
            "content": content
        })
        st.write(f"Uploaded: {uploaded_file.name}")
        st.text_area(f"Resume Content ({uploaded_file.name})", content, height=200)        

# Step 3: Parse, Rank, and Display Top Candidates
if st.button("Find Best Candidates"):
    hr_input_agent = HrInputAgent()
    for resume in resumes:
        hr_input_agent.collect_resume(resume)
    search_agent = SearchAgent(job_description)
    internal_tool = InternalTool()
    resume_parser_agent = ResumeParserAgent(internal_tool)
    ranking_agent = RankingAgent()

    all_resumes = hr_input_agent.get_submissions()
    print("All resumes collected:", all_resumes)
    print("=======================")
    filtered_resumes = search_agent.filter_resumes(all_resumes)
    print("Filtered resumes:", filtered_resumes)
    print("=======================")
    ranked_resumes = search_agent.rank_resumes(filtered_resumes)
    print("ranked resumes", ranked_resumes)
    print("=======================")
    st.header("Top Candidates")
    for idx, candidate in enumerate(ranked_resumes, 1):
        st.subheader(f"Rank #{idx}: {candidate.get('name')}")
        st.write(f"Email Address: {candidate.get('email')}")
        st.write(f"Match Score: {candidate.get('match_score', 0)}")
        st.write(f"Education Match: {candidate.get('education_match', 'Unknown')}")
        st.write(f"Matched Skills: {candidate.get('matched_skills')}")
        st.write(f"Missing Skills: {candidate.get('missing_skills')}")
        st.write(f"Experience Relevance Score: {candidate.get('experience_relevance_score', 0)}")
        st.write(f"Explanation: {candidate.get('explanation', 'No explanation provided')}")        
    st.markdown("---")
    st.success("Ranking completed successfully!")