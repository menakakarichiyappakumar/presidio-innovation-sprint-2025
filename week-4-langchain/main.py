import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import AgentExecutor, create_react_agent
from langchain import hub
from langchain_core.tools import Tool

# For RAG Tool
from langchain_community.document_loaders import TextLoader, DirectoryLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter

# For Web Search Tool
from langchain_tavily import TavilySearch

# For Google Docs Tool (MCP Tool)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

load_dotenv()

# Set up API keys
os.environ["TAVILY_API_KEY"] = os.getenv("TAVILY_API_KEY")
os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY") # For Gemini

# Initialize LLM
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

# --- RAG Tool for HR Policy Documents ---
VECTOR_DB_PATH = "faiss_index"

def create_vector_db(data_path="hr_policies"):
    if not os.path.exists(data_path) or not os.listdir(data_path):
        print(f"No documents found in {data_path}. Please add PDF documents to this directory for the RAG tool.")
        return None

    loader = DirectoryLoader(data_path, glob="./*.txt", loader_cls=TextLoader)
    documents = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    texts = text_splitter.split_documents(documents)
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    db = FAISS.from_documents(texts, embeddings)
    db.save_local(VECTOR_DB_PATH)
    print("Vector DB created successfully.")
    return db

def get_vector_db():
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    if os.path.exists(VECTOR_DB_PATH):
        return FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
    else:
        return create_vector_db()

def hr_policy_rag_tool(query: str) -> str:
    """Searches and retrieves answers from HR Policy documents."""
    db = get_vector_db()
    if db is None:
        return "HR policy documents not found or vector database not initialized. Please add documents to the 'hr_policies' directory."
    docs = db.similarity_search(query)
    # A more sophisticated RAG would involve an LLM to synthesize the answer from docs
    # For simplicity, we'll return the content of the most relevant document
    if docs:
        return docs[0].page_content
    return "No relevant HR policy found."

# --- Web Search Tool ---
web_search_tool = TavilySearch()

# --- MCP Tool for Google Docs ---
SCOPES = ['https://www.googleapis.com/auth/documents.readonly', 'https://www.googleapis.com/auth/drive.readonly']
TOKEN_PICKLE_FILE = 'token.pickle'
CREDENTIALS_JSON_FILE = 'credentials.json'

def get_google_docs_service():
    creds = None
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_JSON_FILE):
                return "Google Docs credentials.json not found. Please download it from Google Cloud Console and place it in the project root."
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_JSON_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(creds, token)
    service = build('docs', 'v1', credentials=creds)
    return service

def search_google_docs(query: str) -> str:
    """Searches Google Docs for insurance-related queries for Presidio."""
    service = get_google_docs_service()
    if isinstance(service, str): # Error message returned
        return service

    # This is a simplified search. A real implementation would use Google Drive API to search for documents
    # and then Google Docs API to read content.
    # For demonstration, let's assume we are looking for a specific document or content within documents.
    # This part needs significant expansion to be truly functional.
    # Example: List some documents (requires Google Drive API)
    try:
        drive_service = build('drive', 'v3', credentials=service._http.credentials)
        results = drive_service.files().list(
            q="mimeType='application/vnd.google-apps.document' and name contains 'insurance'",
            pageSize=10, fields="nextPageToken, files(id, name)").execute()
        items = results.get('files', [])

        if not items:
            return "No insurance-related Google Docs found."
        else:
            # For simplicity, let's try to get content of the first document found
            first_doc_id = items[0]['id']
            first_doc_name = items[0]['name']
            
            docs_service = build('docs', 'v1', credentials=service._http.credentials)
            document = docs_service.documents().get(documentId=first_doc_id).execute()
            
            content = ""
            for element in document.get('body', {}).get('content', []):
                if 'paragraph' in element:
                    for run in element['paragraph']['elements']:
                        if 'textRun' in run:
                            content += run['textRun']['content']
            
            if content:
                return f"Content from '{first_doc_name}' (ID: {first_doc_id}):\n{content[:1000]}..." # Return first 1000 chars
            else:
                return f"Found document '{first_doc_name}' (ID: {first_doc_id}), but could not extract content."
    except Exception as e:
        return f"An error occurred while accessing Google Docs: {e}. Make sure you have enabled Google Docs API and Google Drive API in your Google Cloud project."

# Create LangChain Tools
tools = [
    Tool(
        name="HR_Policy_RAG_Tool",
        func=hr_policy_rag_tool,
        description="Useful for searching and retrieving answers from internal HR Policy documents. Input should be a specific question about HR policies."
    ),
    Tool(
        name="Web_Search_Tool",
        func=web_search_tool.run,
        description="Useful for fetching industry benchmarks, trends, and regulatory updates from the web. Input should be a clear search query."
    ),
    Tool(
        name="Google_Docs_MCP_Tool",
        func=search_google_docs,
        description="Useful for answering insurance-related queries by searching Google Docs. Requires 'credentials.json' for authentication. Input should be an insurance-related query."
    )
]

# Construct the ReAct agent
prompt = hub.pull("hwchase17/react")

agent = create_react_agent(llm, tools, prompt)

agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

if __name__ == "__main__":
    print("Initializing HR Policy Vector DB...")
    create_vector_db() # Ensure vector DB is created/loaded on startup

    print("\nInternal Research Agent Ready!")
    print("You can ask questions like:")
    print("- Summarize all customer feedback related to our Q1 marketing campaigns.")
    print("- Compare our current hiring trend with industry benchmarks.")
    print("- Find relevant compliance policies related to AI data handling.")
    print("- What is our policy on remote work?")
    print("- Search Google Docs for 'employee insurance benefits'.")

    while True:
        query = input("\nEnter your query (type 'exit' to quit): ")
        if query.lower() == 'exit':
            break
        try:
            response = agent_executor.invoke({"input": query})
            print("\nAgent Response:")
            print(response["output"])
        except Exception as e:
            print(f"An error occurred: {e}")
            print("Please ensure all API keys are set and Google Docs 'credentials.json' is in place if you are using the Google Docs tool.")
