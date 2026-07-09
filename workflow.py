from agents import DiagnosisAgent
from langchain_openrouter import ChatOpenRouter
from tools import (
    list_namespaces,
    get_resource,
    list_resources,
    describe_resource,
    get_events,
    get_pod_logs,
    get_previous_logs,
    top_pods,
    top_nodes,
    rollout_status,
)
import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    llm = ChatOpenRouter(
        model="qwen/qwen3-coder-next",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    tools = [
        list_namespaces,
        get_resource,
        list_resources,
        describe_resource,
        get_events,
        get_pod_logs,
        get_previous_logs,
        top_pods,
        top_nodes,
        rollout_status,
    ]
    diagnosis_agent = DiagnosisAgent(llm, tools)
    result = diagnosis_agent.invoke({
        "messages": [],
        "query": "What is the rollout status of the deployment in the default namespace",
        "iteration_count": 0
    })

    print(result["messages"][-1].content)