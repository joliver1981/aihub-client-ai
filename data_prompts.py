DATA_INPUT_VALIDATION_SYSTEM = """
You are an AI assistant that validates user inputs by checking their relevance against the database schema and the descriptions of the tables. 
This ensures that user queries are pertinent to the data.
"""

DATA_INPUT_VALIDATION_PROMPT = """
#### Context:
You are a validation assistant. Your job is to validate user inputs and ensure they are relevant to the data available in the database. 
Below is the schema of the database and a description of the tables it contains. 
Please verify if the user's input is relevant based on this information.

Database Schema:
{schema}

Table Descriptions:
{table_descriptions}

User Input:
{user_question}

#### Instructions:
Is the user's input relevant to the data available? If yes, provide a brief reason why. If no, suggest a more relevant question or inform the user about the irrelevant input.
The evaluation should include a confidence level, from 0 to 100, regarding the certainty of your answer.

#### Output Format:
Return a JSON string with the following elements:

- "relevant": "yes" or "no"
- "response": string
- "confidence": integer (0-100)
"""

DATA_INPUT_CLASSIFICATION_SYSTEM = """
You are a user input classification assistant. Your job is to classify user inputs into categories based on the context provided. 
"""

DATA_INPUT_CLASSIFICATION_PROMPT = """
#### Context:
Below are the recent conversation history, current user input, database schema, and descriptions of the tables. 
Based on this information, classify the current user input into one of the following categories: 'New Question', 'Follow-Up Question', 'Response to Request for More Information', or 'Irrelevant'.

Recent Conversation History:
{conversation_history}

Database Schema:
{schema}

Table Descriptions:
{table_descriptions}

Current User Input:
{user_question}

#### Evaluation Criteria
1. **New Topic Identification**: If the user's input introduces a topic not previously discussed, classify it as an 'New Question'.
2. **Contextual Continuity**: If the user's input is directly related to previous questions or responses, classify it as a 'Follow-Up Question'.
3. **Response to a Prompt**: If the user's input is answering a specific request from the assistant for more information, classify it as 'Response to Request for More Information'.
4. **Ambiguity and Overlap**: If the input seems ambiguous or could fit into more than one category, consider the most immediate context and the user's intent based on recent history.
5. **Irrelevant**: If the user's input seems completely irrelevant and unrelated to the data and recent conversation, classify it as 'Irrelevant'.

#### Instructions:
Please classify the current user input and provide a brief explanation for your classification. If the classification is 'Irrelevant', provide a polite response as the explanation.
The evaluation should include a confidence level, from 0 to 100, regarding the certainty of your answer.

#### Output Format:
Return the response as a JSON string with following keys: 'classification', 'explanation', and 'confidence'.

- "classification": "New Question" or "Follow-Up Question" or "Response to Request for More Information" or "Irrelevant"
- "explanation": string
- "confidence": integer (0-100)
"""


# Function to override config parameters from user_prompts.py if it exists
def load_user_prompts():
    try:
        import user_data_prompts
        globals().update({key: value for key, value in user_data_prompts.__dict__.items() if not key.startswith('__')})
    except ImportError:
        pass

# Load user-defined configuration parameters
load_user_prompts()


# ============================================================================
# AIHUB_PROMPT_OVERRIDE_HOOK - admin system-prompt overrides (additive)
# ----------------------------------------------------------------------------
# Overlays admin-set prompts from data/prompt_overrides.json on top of the
# defaults defined above, so they can be edited from the System Prompts admin
# screen (/settings/system-prompts) without changing code.
#
#   * No override file  -> this is a no-op and behaviour is unchanged.
#   * Fails open        -> any problem leaves the code defaults untouched.
#   * Nothing above this line is modified, and reverting an override in the UI
#     restores the shipped default exactly.
#
# See prompt_overrides.py for the validation rules (a value must be a string
# and must keep every {placeholder} the default relies on).
# ============================================================================
try:
    try:
        from prompt_overrides import apply_prompt_overrides as _po_apply
    except ImportError:
        # The repo root is not on sys.path in this service's process. Load the
        # module straight off disk rather than mutating sys.path, so import
        # resolution for this process is left exactly as it was.
        import os as _po_os
        import importlib.util as _po_ilu
        _po_apply = None
        _po_dir = _po_os.path.dirname(_po_os.path.abspath(__file__))
        for _po_i in range(6):
            _po_file = _po_os.path.join(_po_dir, 'prompt_overrides.py')
            if _po_os.path.isfile(_po_file):
                _po_spec = _po_ilu.spec_from_file_location(
                    '_aihub_prompt_overrides', _po_file)
                _po_mod = _po_ilu.module_from_spec(_po_spec)
                _po_spec.loader.exec_module(_po_mod)
                _po_apply = _po_mod.apply_prompt_overrides
                break
            _po_parent = _po_os.path.dirname(_po_dir)
            if _po_parent == _po_dir:
                break
            _po_dir = _po_parent
    if _po_apply:
        _po_apply(globals(), 'data_prompts.py')
except Exception:
    pass
