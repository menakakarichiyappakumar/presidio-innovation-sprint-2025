import os
from langchain_core.tools import tool
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

@tool
def read_file(file_path: str) -> str:
    """Reads the content of a file."""
    try:
        # Construct an absolute path relative to the current file (tools.py)
        absolute_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), file_path))
        with open(absolute_file_path, 'r') as f:
            content = f.read()
        return content
    except FileNotFoundError:
        return f"Error: File not found at {file_path} (resolved to {absolute_file_path})"
    except Exception as e:
        # Print the full exception for debugging
        import traceback
        traceback.print_exc()
        return f"Error reading file: {e}"

@tool
def web_search(query: str) -> str:
    """Performs a web search using Tavily API and returns the summary of the results."""
    try:
        tavily_api_key = os.getenv("TAVILY_API_KEY")
        if not tavily_api_key:
            return "Error: TAVILY_API_KEY not found in environment variables. Please set it up."

        client = TavilyClient(api_key=tavily_api_key)
        response = client.search(query=query, search_depth="basic")

        # Extracting relevant information from the search results
        results_summary = []
        if response and response.get('results'):
            for result in response['results']:
                results_summary.append(f"Title: {result.get('title')}\nURL: {result.get('url')}\nContent: {result.get('content')}\n")
            return "\n---\n".join(results_summary)
        else:
            return "No search results found."

    except Exception as e:
        return f"Error during web search with Tavily: {e}"
