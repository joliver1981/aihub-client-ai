import requests
import logging
from typing import List, Dict, Union
from bs4 import BeautifulSoup
from langchain_core.tools import Tool
import config as cfg
from AppUtils import azureQuickPrompt


# --- Logger Setup ---
logger = logging.getLogger("WebSearch")
logger.setLevel(logging.INFO)

if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Core WebSearch Class ---
class WebSearch:
    def __init__(self, api_key: str = cfg.DEFAULT_INTERNET_SEARCH_KEY, default_engine: str = cfg.DEFAULT_INTERNET_SEARCH):
        self.api_key = api_key
        self.default_engine = default_engine.lower()

    def search(self, query: str, num_results: int = 5) -> List[Dict]:
        if self.default_engine == "duckduckgo":
            logger.info("Using DuckDuckGo as default search engine.")
            return self._search_duckduckgo(query, num_results)

        try:
            if self.default_engine == "tavily":
                return self._search_tavily(query, num_results)
            else:
                raise ValueError(f"Unknown search engine '{self.default_engine}'")
        except Exception as e:
            logger.warning(f"Primary search engine '{self.default_engine}' failed: {e}")
            print("⚠️ Falling back to DuckDuckGo...")
            return self._search_duckduckgo(query, num_results)
        
    def search_ai(self, query: str, num_results: int = 5) -> Dict:
        """
        Search the web and return both raw results and an AI-generated answer.
        
        For Tavily, uses the built-in AI answer.
        For other engines, generates an AI answer by analyzing the search results.
        
        Args:
            query: The search query string
            num_results: Maximum number of search results to return
            
        Returns:
            Dict containing:
            - 'answer': AI-generated answer to the query
            - 'results': List of search result dictionaries
        """
        try:
            if self.default_engine == "tavily":
                logger.info(f"Using Tavily search with AI answer for query: {query}")
                search_results = self._search_tavily(query, num_results)
                
                # Extract the AI answer if it exists (should be in the first result)
                ai_answer = ""
                if search_results and "ai_answer" in search_results[0]:
                    ai_answer = search_results[0].get("ai_answer", "")
                    # Remove the AI answer entry from results to avoid duplication
                    search_results = search_results[1:] if len(search_results) > 1 else []
                
                print('Tavily Search Answer:', ai_answer)
                return {
                    "ai_answer": ai_answer,
                    "results": search_results
                }
            else:
                # For other search engines, get results and then use AI to analyze
                logger.info(f"Using {self.default_engine} search with post-processing AI analysis for query: {query}")
                search_results = self.search(query, num_results)
                
                # Format search results for AI analysis
                formatted_results = "\n\n".join([
                    f"Result {i+1}:\nTitle: {r['title']}\nLink: {r['link']}\nSnippet: {r['snippet']}" 
                    for i, r in enumerate(search_results)
                ])
                
                # Generate AI answer using azureQuickPrompt
                system_prompt = """
                You are a helpful assistant that provides concise, accurate answers based on search results.
                Analyze the search results provided and give a clear, direct answer to the original query.
                Keep your answer brief (2-3 sentences) and focused on the most relevant information.
                If the search results don't contain a clear answer, say so honestly.
                """
                
                user_prompt = f"""
                Original search query: {query}
                
                Search results:
                {formatted_results}
                
                Based on these search results, provide a concise answer to the original query.
                """
                
                ai_answer = azureQuickPrompt(prompt=user_prompt, system=system_prompt, use_alternate_api=True)
                
                return {
                    "ai_answer": ai_answer,
                    "results": search_results
                }
        except Exception as e:
            logger.error(f"Error in search_ai: {e}")
            # Fall back to DuckDuckGo and AI analysis
            try:
                search_results = self._search_duckduckgo(query, num_results)
                
                # Format search results for AI analysis
                formatted_results = "\n\n".join([
                    f"Result {i+1}:\nTitle: {r['title']}\nLink: {r['link']}\nSnippet: {r['snippet']}" 
                    for i, r in enumerate(search_results)
                ])
                
                # Generate AI answer using azureQuickPrompt
                system_prompt = """
                You are a helpful assistant that provides concise, accurate answers based on search results.
                Analyze the search results provided and give a clear, direct answer to the original query.
                Keep your answer brief (2-3 sentences) and focused on the most relevant information.
                If the search results don't contain a clear answer, say so honestly.
                """
                
                user_prompt = f"""
                Original search query: {query}
                
                Search results:
                {formatted_results}
                
                Based on these search results, provide a concise answer to the original query.
                """
                
                ai_answer = azureQuickPrompt(prompt=user_prompt, system=system_prompt, use_alternate_api=True)
                
                return {
                    "ai_answer": ai_answer,
                    "results": search_results
                }
            except Exception as nested_e:
                logger.error(f"Error in fallback search: {nested_e}")
                return {
                    "ai_answer": f"Unable to generate an answer due to search errors: {str(e)}",
                    "results": []
                }

    def _search_tavily(self, query: str, num_results: int) -> List[Dict]:
        url = "https://api.tavily.com/search"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "query": query,
            "include_answer": "basic",
            #"search_depth": "advanced",
            "max_results": num_results
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        return [
            {
                "ai_answer": data.get("answer", '') if data.get("answer", '') != '' else ''
            }
        ] + [
            {
                "title": r.get("title"),
                "link": r.get("url"),
                "snippet": r.get("content"),
            }
            for r in data.get("results", [])[:num_results]
        ]

    def _search_duckduckgo(self, query: str, num_results: int) -> List[Dict]:
        url = f"https://lite.duckduckgo.com/lite/?q={query.replace(' ', '+')}"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a", attrs={"class": "result-link"}, limit=num_results)

        results = []
        for link in links:
            title = link.text.strip()
            href = link.get("href")
            snippet_tag = link.find_next("div", class_="result-snippet")
            results.append({
                "title": title,
                "link": href,
                "snippet": snippet_tag.text.strip() if snippet_tag else ""
            })

        return results


def get_web_search_tool(api_key: str = None, default_engine: str = "tavily") -> Tool:
    web_search = WebSearch(api_key=api_key, default_engine=default_engine)

    def run(query: str) -> str:
        results = web_search.search(query)
        return "\n\n".join([
            f"{r['title']}\n{r['link']}\n{r['snippet']}" for r in results
        ])

    return Tool(
        name="WebSearch",
        func=run,
        description=f"Use this tool to get live web search results. Default engine is {default_engine.upper()}."
    )
