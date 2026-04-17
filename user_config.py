REFINE_TABLE_SELECTION = False
ALLOW_INTERNET_SEARCH_FOR_NLQ = False
MAX_CONVERSATION_LENGTH = 10
DATA_AGENT_FALLBACK_RESPONSE = "I'm sorry, I encountered an issue processing your request. Please try a different question or contact support if the problem persists."
AI_HUB_BYPASS_DOCUMENT_PROXY = False
SHOW_DATA_AGENT_TEST_FEATURES = True
ENABLE_CAUTION_SYSTEM = False
DEFAULT_CAUTION_LEVEL = 'medium'
USE_MINI_MODELS_WHEN_POSSIBLE = False
ENABLE_RESPONSE_FILTER = True
VECTOR_EMBEDDING_MODEL = 'openai'
DOC_INCLUDE_SNIPPET_IN_RESULT = True                        # Include/exclude snippets or summaries from page text (burns tokens)
DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS = 500                  # Top fields to consider (balance between useful versus noise)
DOC_PAGE_TEXT_LIMIT_IN_RESULTS = 1000                       # Character limit for snippet (if not using summaries)
DOC_ENABLE_AUTO_SUMMARIZATION = False                       # Create document summaries during initial processing
DOC_SEARCH_ENABLE_SUMMARIES = False                         # Global toggle for summary usage in search
DOC_SEARCH_DEFAULT_SUMMARY_TYPE = 'detailed'                # Default summary type to use for search
DOC_SEARCH_USE_SUMMARIES_BY_DOCTYPE = {                     # Enable/disable search summaries for each document type
    'lease_agreement': False,
}
USE_COMBINED_ANALYSIS = False                               # Combine certain prompts for NLQ (slight performance gain)
USE_OPENAI_API = False                                      # Use the OpenAI API instead of Azure API
SHOW_EXPERIMENTAL_FEATURES = True
