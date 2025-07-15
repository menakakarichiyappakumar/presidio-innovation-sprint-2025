import os
import operator
from typing import Annotated, Sequence, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from tools import read_file, web_search # Import the tools
from dotenv import load_dotenv
import functools # Import functools for @wraps

load_dotenv() # Load environment variables from .env file


# Decorator for logging agent activity
def log_agent_activity(func):
    @functools.wraps(func)
    def wrapper(self, state: AgentState):
        agent_name = self.__class__.__name__
        print(f"\n--- {agent_name} - Executing {func.__name__} ---")
        print(f"Input Query: {state['input']}")
        result = func(self, state)
        print(f"--- {agent_name} - Finished {func.__name__} ---")
        return result
    return wrapper

# Define the state of the graph
class AgentState(TypedDict):
    input: str
    chat_history: Annotated[Sequence[BaseMessage], operator.add]
    agent_outcome: str
    next_agent: str

# Initialize the LLM
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

# Define the Supervisor Agent
class SupervisorAgent:
    def __init__(self, llm):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a supervisor agent. Your role is to classify the user's query as either 'IT' or 'Finance'. Respond with only 'IT' or 'Finance'."),
            ("user", "{input}")
        ])
        self.chain = self.prompt | self.llm

    @log_agent_activity
    def route_query(self, state: AgentState):
        query = state["input"]
        response = self.chain.invoke({"input": query})
        classification = response.content.strip()
        print(f"Supervisor classified query as: {classification}")
        return {"next_agent": classification}

# Define the IT Agent
class ITAgent:
    """
    Purpose: Handles all IT-related queries.
    Tools:
    - ReadFile for internal IT documentation (e.g., 'it_docs.txt')
    - WebSearch for external information sources
    """
    def __init__(self, llm, tools):
        self.llm = llm
        self.tools = tools
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an IT support agent. Your purpose is to handle all IT-related queries. You can use the 'read_file' tool for internal IT documentation (e.g., 'it_docs.txt') and 'web_search' for external information sources. Answer the user's IT-related queries concisely and accurately."),
            ("user", "{input}")
        ])
        self.chain = self.prompt | self.llm.bind_tools(tools=self.tools)

    @log_agent_activity
    def handle_query(self, state: AgentState):
        query = state["input"]
        tool_output = ""

        print(f"IT Agent attempting to read ./docs/it_docs.txt for: {query}")
        tool_output = read_file.invoke({'file_path': './docs/it_docs.txt'})
        
        should_web_search = False
        read_file_content = ""

        # Check if read_file provided a relevant answer
        if "Error: File not found" in tool_output or len(tool_output) == 0:
            print("IT Agent: Error reading it_docs.txt or file is empty. Proceeding to web search.")
            should_web_search = True
        else:
            # Ask the LLM to evaluate if the content is relevant
            relevance_check_prompt = ChatPromptTemplate.from_messages([
                ("system", f"Given the original query: '{query}', and the following content from internal documentation:\n\n{tool_output}\n\nIs this content relevant and sufficient to answer the query? Respond with 'YES' if relevant and sufficient, 'NO' otherwise."),
                ("user", "Evaluate relevance.")
            ])
            relevance_response = (relevance_check_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content.strip().upper()

            if relevance_response == "YES":
                print("IT Agent: Information found and deemed relevant in it_docs.txt.")
                final_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are an IT support agent. Based on the following internal documentation and the original query, provide a clear, neat, and detailed answer. Extract all relevant information and present it in an easy-to-understand format."),
                    ("user", f"Original query: {query}\nTool Output: {tool_output}")
                ])
                final_response_content = (final_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content
                return {"agent_outcome": final_response_content}
            else:
                print("IT Agent: Information from it_docs.txt not deemed relevant/sufficient. Proceeding to web search.")
                should_web_search = True
                read_file_content = tool_output # Keep content for context if needed by web search prompt

        if should_web_search:
            print(f"IT Agent: Attempting web_search for: {query}")
            web_search_chain = ChatPromptTemplate.from_messages([
                ("system", "You are an IT support agent. You need to find information for the user's query using 'web_search' for external information. Provide a tool call to 'web_search'. If internal documentation was provided but not sufficient, consider that context."),
                ("user", f"Original query: {query}\nInternal Doc Context (if any): {read_file_content}")
            ]) | self.llm.bind_tools(tools=[web_search])

            response_web_search = web_search_chain.invoke({"input": query})

            if isinstance(response_web_search, AIMessage) and response_web_search.tool_calls:
                for tool_call in response_web_search.tool_calls:
                    if tool_call['name'] == "web_search":
                        print(f"IT Agent: Calling web_search for: {query}")
                        tool_output = web_search.invoke(tool_call['args'])
                        
                        final_prompt = ChatPromptTemplate.from_messages([
                            ("system", "You are an IT support agent. Based on the following web search results and the original query, provide a complete and concise answer."),
                            ("user", f"Original query: {query}\nWeb Search Output: {tool_output}")
                        ])
                        final_response_content = (final_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content
                        return {"agent_outcome": final_response_content}
            
            # Fallback if web search also doesn't yield a tool call or relevant info
            print("IT Agent: Web search did not yield a relevant tool call. Providing direct answer if possible.")
            # If the response_web_search was not a tool call, it's a direct answer from the LLM.
            if isinstance(response_web_search, AIMessage) and not response_web_search.tool_calls:
                return {"agent_outcome": response_web_search.content}
            else:
                # If no tool was called and no direct answer from web_search_chain,
                # then use a general prompt to try to answer directly.
                direct_answer_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are an IT support agent. No relevant information was found using internal documentation or web search. Please provide a direct answer to the user's query based on your general knowledge, or state that you cannot find the information."),
                    ("user", "{input}")
                ])
                direct_answer_chain = direct_answer_prompt | self.llm
                direct_response_content = direct_answer_chain.invoke({"input": query}).content
                return {"agent_outcome": direct_response_content}
        
        # This part should ideally not be reached if read_file was sufficient
        print("IT Agent: Unexpected flow - read_file was sufficient but not returned.")
        return {"agent_outcome": "An unexpected error occurred in IT Agent's tool handling."}

# Define the Finance Agent
class FinanceAgent:
    def __init__(self, llm, tools):
        self.llm = llm
        self.tools = tools
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a Finance support agent. You can use the 'read_file' tool for internal finance documentation (e.g., 'finance_docs.txt') and 'web_search' for public finance data. Answer the user's finance-related queries concisely."),
            ("user", "{input}")
        ])
        self.chain = self.prompt | self.llm.bind_tools(tools=self.tools)

    @log_agent_activity
    def handle_query(self, state: AgentState):
        query = state["input"]
        tool_output = ""
        
        print(f"Finance Agent attempting to read ./docs/finance_docs.txt for: {query}")
        tool_output = read_file.invoke({'file_path': './docs/finance_docs.txt'})
        
        should_web_search = False
        read_file_content = ""

        # Check if read_file provided a relevant answer
        if "Error: File not found" in tool_output or len(tool_output) == 0:
            print("Finance Agent: Error reading finance_docs.txt or file is empty. Proceeding to web search.")
            should_web_search = True
        else:
            # Ask the LLM to evaluate if the content is relevant
            relevance_check_prompt = ChatPromptTemplate.from_messages([
                ("system", f"Given the original query: '{query}', and the following content from internal documentation:\n\n{tool_output}\n\nIs this content relevant and sufficient to answer the query? Respond with 'YES' if relevant and sufficient, 'NO' otherwise."),
                ("user", "Evaluate relevance.")
            ])
            relevance_response = (relevance_check_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content.strip().upper()

            if relevance_response == "YES":
                print("Finance Agent: Information found and deemed relevant in finance_docs.txt.")
                final_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are a Finance support agent. Based on the following internal documentation and the original query, provide a complete and concise answer."),
                    ("user", f"Original query: {query}\nInternal Doc Output: {tool_output}")
                ])
                final_response_content = (final_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content
                return {"agent_outcome": final_response_content}
            else:
                print("Finance Agent: Information from finance_docs.txt not deemed relevant/sufficient. Proceeding to web search.")
                should_web_search = True
                read_file_content = tool_output # Keep content for context if needed by web search prompt

        if should_web_search:
            print(f"Finance Agent: Attempting web_search for: {query}")
            web_search_chain = ChatPromptTemplate.from_messages([
                ("system", "You are a Finance support agent. You need to find information for the user's query using 'web_search' for public finance data. Provide a tool call to 'web_search'. If internal documentation was provided but not sufficient, consider that context."),
                ("user", f"Original query: {query}\nInternal Doc Context (if any): {read_file_content}")
            ]) | self.llm.bind_tools(tools=[web_search])

            response_web_search = web_search_chain.invoke({"input": query})

            if isinstance(response_web_search, AIMessage) and response_web_search.tool_calls:
                for tool_call in response_web_search.tool_calls:
                    if tool_call['name'] == "web_search":
                        print(f"Finance Agent: Calling web_search for: {query}")
                        tool_output = web_search.invoke(tool_call['args'])
                        
                        final_prompt = ChatPromptTemplate.from_messages([
                            ("system", "You are a Finance support agent. Based on the following web search results and the original query, provide a complete and concise answer."),
                            ("user", f"Original query: {query}\nWeb Search Output: {tool_output}")
                        ])
                        final_response_content = (final_prompt | self.llm).invoke({"input": query, "tool_output": tool_output}).content
                        return {"agent_outcome": final_response_content}
            
            # Fallback if web search also doesn't yield a tool call or relevant info
            print("Finance Agent: Web search did not yield a relevant tool call. Providing direct answer if possible.")
            if isinstance(response_web_search, AIMessage) and not response_web_search.tool_calls:
                return {"agent_outcome": response_web_search.content}
            else:
                direct_answer_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are a Finance support agent. No relevant information was found using internal documentation or web search. Please provide a direct answer to the user's query based on your general knowledge, or state that you cannot find the information."),
                    ("user", "{input}")
                ])
                direct_answer_chain = direct_answer_prompt | self.llm
                direct_response_content = direct_answer_chain.invoke({"input": query}).content
                return {"agent_outcome": direct_response_content}
        
        print("Finance Agent: Unexpected flow - read_file was sufficient but not returned.")
        return {"agent_outcome": "An unexpected error occurred in Finance Agent's tool handling."}

# Build the LangGraph
workflow = StateGraph(AgentState)

# Initialize agents
supervisor_agent = SupervisorAgent(llm)
it_agent = ITAgent(llm, tools=[read_file, web_search])
finance_agent = FinanceAgent(llm, tools=[read_file, web_search])

# Add nodes
workflow.add_node("supervisor", supervisor_agent.route_query)
workflow.add_node("it_agent", it_agent.handle_query)
workflow.add_node("finance_agent", finance_agent.handle_query)

# Set entry point
workflow.set_entry_point("supervisor")

# Add conditional edges
workflow.add_conditional_edges(
    "supervisor",
    lambda state: state["next_agent"],
    {"IT": "it_agent", "Finance": "finance_agent"}
)

workflow.add_edge("it_agent", END)
workflow.add_edge("finance_agent", END)

# Compile the graph
app = workflow.compile()

# Main execution loop
if __name__ == "__main__":
    # Ensure GOOGLE_API_KEY is set
    if not os.getenv("GOOGLE_API_KEY"):
        print("Error: GOOGLE_API_KEY environment variable not set.")
        print("Please set it in your .env file or directly in your environment.")
        print("Example: export GOOGLE_API_KEY='your_api_key_here'")
        exit(1)

    print("Multi-Agent Support System Ready!")
    print("Type 'exit' to quit.")

    while True:
        user_query = input("You: ")
        if user_query.lower() == 'exit':
            break
        
        inputs = {"input": user_query, "chat_history": []}
        
        # Collect all output into a list of strings
        chat_output_lines = []
        for s in app.stream(inputs):
            if "__end__" not in s:
                # Extract and format relevant information from the state changes
                for key, value in s.items():
                    if key == "supervisor":
                        chat_output_lines.append(f"Supervisor Action: Routed to {value.get('next_agent')}")
                    elif key == "it_agent":
                        # Extract agent_outcome if available, otherwise print the whole state
                        outcome = value.get('agent_outcome')
                        if outcome:
                            chat_output_lines.append(f"IT Agent Response: {outcome}")
                        else:
                            chat_output_lines.append(f"IT Agent State: {value}")
                    elif key == "finance_agent":
                        # Extract agent_outcome if available, otherwise print the whole state
                        outcome = value.get('agent_outcome')
                        if outcome:
                            chat_output_lines.append(f"Finance Agent Response: {outcome}")
                        else:
                            chat_output_lines.append(f"Finance Agent State: {value}")
                    else:
                        chat_output_lines.append(f"State Change: {key}: {value}")
            else:
                final_output = s["__end__"]["agent_outcome"]
                chat_output_lines.append(f"Agent Final Response: {final_output}")
        
        # Join all collected lines into a single string and print
        full_chat_output = "\n".join(chat_output_lines)
        print(full_chat_output)
        print("-" * 50)
