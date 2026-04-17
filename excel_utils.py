# excel_utils.py

"""
Excel Template Population Utilities

Provides intelligent Excel template detection, AI-powered data mapping,
and population using Pandas + openpyxl.

Three schema modes:
1. Existing Template - Read schema from Excel file
2. User-Defined - Schema provided in config  
3. AI-Generated - AI determines schema from input data

Two template types (auto-detected):
- Table: Headers in row 1, data appended as rows
- Form: Labels with adjacent value cells
"""

import pandas as pd
import json
import os
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, List, Any, Optional, Tuple, Union
from openpyxl import load_workbook, Workbook
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import PatternFill, Font
import re
from CommonUtils import rotate_logs_on_startup, get_log_path
import system_prompts as sysprompts


# Configure logging
def setup_logging():
    """Configure logging for the excel utils"""
    logger = logging.getLogger("ExcelUtils")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('EXCEL_UTILS_LOG', get_log_path('excel_utils_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('EXCEL_UTILS_LOG', get_log_path('excel_utils_log.txt')))

logger = setup_logging()

try:
    from AppUtils import azureMiniQuickPrompt
    from AppUtils import quickPrompt
except ImportError:
    logger.warning("Could not import quickPrompt from AppUtils. AI features might be unavailable.")
    from AppUtils import azureQuickPrompt as quickPrompt


def detect_template_type(ws) -> str:
    """
    Auto-detect whether a worksheet is table-style or form-style using AI.
    
    Table-style: Headers in row 1, data in subsequent rows (columnar layout)
    Form-style: Labels with adjacent value cells (scattered layout)
    
    Args:
        ws: openpyxl worksheet object
        
    Returns:
        "table" or "form"
    """
    logger.debug("Detecting template type...")
    
    # Get dimensions
    max_row = ws.max_row
    max_col = ws.max_column
    
    logger.debug(f"Worksheet dimensions: {max_row} rows x {max_col} columns")
    
    # Quick heuristic for obvious cases
    if max_row <= 1:
        logger.debug("Only 1 row detected, assuming table type with headers only")
        return "table"
    
    # Build a sample of the worksheet structure for AI analysis
    sample_data = []
    
    # Get first few rows (up to 5 rows, up to 20 columns)
    rows_to_check = min(max_row, 5)
    cols_to_check = min(max_col, 20)
    
    for row in range(1, rows_to_check + 1):
        row_data = []
        for col in range(1, cols_to_check + 1):
            cell_value = ws.cell(row=row, column=col).value
            if cell_value is not None:
                # Truncate long values
                cell_str = str(cell_value)[:50]
                row_data.append(f"[{row},{col}]: {cell_str}")
            else:
                row_data.append(f"[{row},{col}]: (empty)")
        sample_data.append(" | ".join(row_data))
    
    sample_text = "\n".join(sample_data)
    
    # Use AI to detect template type
    if quickPrompt:
        try:
            result = _ai_detect_template_type(sample_text, max_row, max_col)
            logger.debug(f"AI detected template type: {result}")
            return result
        except Exception as e:
            logger.warning(f"AI template detection failed: {str(e)}, falling back to heuristic")
            return _heuristic_detect_template_type(ws, max_row, max_col)
    else:
        logger.debug("quickPrompt not available, using heuristic detection")
        return _heuristic_detect_template_type(ws, max_row, max_col)


def _ai_detect_template_type(sample_text: str, max_row: int, max_col: int) -> str:
    """
    Use AI to detect whether the template is table-style or form-style.
    
    Args:
        sample_text: Sample of worksheet cell values
        max_row: Total rows in worksheet
        max_col: Total columns in worksheet
        
    Returns:
        "table" or "form"
    """
    logger.debug("Using AI to detect template type...")
    
    system_prompt = """You are an expert at analyzing Excel spreadsheet structures.
Your task is to determine if a spreadsheet is TABLE-style or FORM-style.

TABLE-style characteristics:
- Row 1 contains column headers (field names)
- Data rows are below the headers
- Each row represents a record
- Columns represent fields/attributes
- Example: Customer | Date | Amount | Status (with data rows below)

FORM-style characteristics:
- Labels and values are scattered across the sheet
- Often has label-value pairs side by side or stacked
- Single record with fields in various positions
- Example: "Name:" in A1 with value in B1, "Date:" in A2 with value in B2

You must respond with ONLY a single word: either "table" or "form"
Do not include any explanation or additional text."""

    user_prompt = f"""Analyze this Excel spreadsheet sample and determine if it's TABLE-style or FORM-style.

Worksheet dimensions: {max_row} rows x {max_col} columns

Sample data (first few rows and columns):
{sample_text}

Based on this structure, is this a TABLE or FORM layout? Respond with only "table" or "form"."""

    response = azureMiniQuickPrompt(
        user_prompt,
        system_prompt,
        temperature=0
    )
    
    if response:
        result = response.strip().lower()
        if result in ("table", "form"):
            return result
        else:
            logger.warning(f"AI returned unexpected value: {result}, defaulting to table")
            return "table"
    else:
        logger.warning("AI returned empty response, defaulting to table")
        return "table"


def _heuristic_detect_template_type(ws, max_row: int, max_col: int) -> str:
    """
    Fallback heuristic detection for template type.
    
    Args:
        ws: openpyxl worksheet object
        max_row: Total rows in worksheet
        max_col: Total columns in worksheet
        
    Returns:
        "table" or "form"
    """
    logger.debug("Using heuristic template type detection...")
    
    # Check row 1 for potential headers
    row1_values = [ws.cell(row=1, column=c).value for c in range(1, max_col + 1)]
    row1_filled = sum(1 for v in row1_values if v is not None and str(v).strip())
    
    logger.debug(f"Row 1 has {row1_filled} filled cells out of {max_col}")
    
    # Check for label-value pattern
    label_value_pairs = 0
    for row in range(1, min(max_row + 1, 20)):
        for col in range(1, max_col):
            cell_val = ws.cell(row=row, column=col).value
            next_cell_val = ws.cell(row=row, column=col + 1).value
            
            if cell_val and isinstance(cell_val, str):
                cell_str = str(cell_val).strip()
                if cell_str.endswith(':') or (len(cell_str) > 2 and next_cell_val is None):
                    label_value_pairs += 1
    
    logger.debug(f"Detected {label_value_pairs} potential label-value pairs")
    
    # Decision logic - more lenient for table detection
    row1_percentage = row1_filled / max_col if max_col > 0 else 0
    
    # Consider it a table if row 1 has multiple headers
    # Use lower threshold (30%) or absolute count (10+)
    if row1_filled >= 3 and (row1_percentage >= 0.3 or row1_filled >= 10):
        logger.debug("Detected as TABLE type (row 1 appears to be headers)")
        return "table"
    elif label_value_pairs >= 3 and row1_filled < 5:
        logger.debug("Detected as FORM type (multiple label-value pairs found)")
        return "form"
    else:
        logger.debug("Defaulting to TABLE type")
        return "table"


def detect_template_schema(template_path: str, sheet_name: str = None) -> Dict:
    """
    Analyze an Excel template and extract its schema.
    Auto-detects whether template is table-style or form-style.
    
    Args:
        template_path: Path to the Excel template file
        sheet_name: Specific sheet to analyze (optional, defaults to active)
    
    Returns:
        {
            "type": "table" | "form",
            "sheet_name": "Sheet1",
            "columns": ["Col A", "Col B", ...],      # for table type
            "data_start_row": 2,                      # for table type
            "cells": {                                # for form type
                "Field Label": {"cell": "B2", "label_cell": "A2"},
                ...
            },
            "raw_structure": {...}                    # for AI context
        }
    """
    logger.info(f"Detecting template schema from: {template_path}")
    
    if not os.path.exists(template_path):
        error_msg = f"Template file not found: {template_path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
    
    try:
        wb = load_workbook(template_path, data_only=True)
        logger.debug(f"Loaded workbook with sheets: {wb.sheetnames}")
        
        # Get target sheet
        requested_sheet_name = sheet_name  # Preserve the originally requested name
        if sheet_name and sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            logger.debug(f"Using specified sheet: {sheet_name}")
        else:
            ws = wb.active
            if not requested_sheet_name:
                sheet_name = ws.title
            else:
                # Sheet doesn't exist yet — use active sheet for schema detection
                # but preserve the requested sheet_name so it gets created later
                logger.debug(f"Sheet '{sheet_name}' not found, using active sheet for schema detection but preserving target name")
            logger.debug(f"Using active sheet: {ws.title}, target sheet_name: {sheet_name}")

        # Detect template type
        template_type = detect_template_type(ws)

        schema = {
            "type": template_type,
            "sheet_name": sheet_name,
            "template_path": template_path
        }
        
        if template_type == "table":
            schema.update(_extract_table_schema(ws))
        else:
            schema.update(_extract_form_schema(ws))
        
        wb.close()
        
        logger.info(f"Schema detection complete. Type: {template_type}")
        logger.debug(f"Full schema: {json.dumps(schema, indent=2, default=str)}")
        
        return schema
        
    except Exception as e:
        logger.error(f"Error detecting template schema: {str(e)}", exc_info=True)
        raise


def _extract_table_schema(ws) -> Dict:
    """
    Extract schema from a table-style worksheet.
    
    Args:
        ws: openpyxl worksheet
        
    Returns:
        Dict with columns, data_start_row, and raw_structure
    """
    logger.debug("Extracting table schema...")
    
    max_col = ws.max_column
    max_row = ws.max_row
    
    # Get headers from row 1
    columns = []
    for col in range(1, max_col + 1):
        cell_value = ws.cell(row=1, column=col).value
        if cell_value is not None and str(cell_value).strip():
            columns.append({
                "name": str(cell_value).strip(),
                "column_letter": get_column_letter(col),
                "column_index": col
            })
    
    logger.debug(f"Found {len(columns)} columns: {[c['name'] for c in columns]}")
    
    # Determine where data starts (usually row 2)
    data_start_row = 2
    
    # Find the last row with data
    last_data_row = 1
    for row in range(2, max_row + 1):
        row_has_data = any(
            ws.cell(row=row, column=c['column_index']).value is not None 
            for c in columns
        )
        if row_has_data:
            last_data_row = row
    
    logger.debug(f"Data starts at row {data_start_row}, last data row: {last_data_row}")
    
    # Build raw structure for AI context
    raw_structure = {
        "headers": [c['name'] for c in columns],
        "sample_data": []
    }
    
    # Get sample data (up to 3 rows)
    for row in range(data_start_row, min(last_data_row + 1, data_start_row + 3)):
        row_data = {}
        for c in columns:
            cell_val = ws.cell(row=row, column=c['column_index']).value
            row_data[c['name']] = cell_val
        if any(v is not None for v in row_data.values()):
            raw_structure["sample_data"].append(row_data)
    
    return {
        "columns": columns,
        "data_start_row": data_start_row,
        "last_data_row": last_data_row,
        "raw_structure": raw_structure
    }


def _extract_form_schema(ws) -> Dict:
    """
    Extract schema from a form-style worksheet using AI to identify fields.
    
    Args:
        ws: openpyxl worksheet
        
    Returns:
        Dict with cells mapping and raw_structure
    """
    logger.debug("Extracting form schema...")
    
    max_row = ws.max_row
    max_col = ws.max_column
    
    # Build a representation of the worksheet for AI analysis
    cell_data = []
    for row in range(1, min(max_row + 1, 50)):  # Limit to first 50 rows
        for col in range(1, min(max_col + 1, 20)):  # Limit to first 20 columns
            cell_value = ws.cell(row=row, column=col).value
            if cell_value is not None:
                cell_data.append({
                    "cell": f"{get_column_letter(col)}{row}",
                    "value": str(cell_value)[:100],  # Truncate long values
                    "row": row,
                    "col": col
                })
    
    logger.debug(f"Found {len(cell_data)} non-empty cells for form analysis")
    
    raw_structure = {
        "cells": cell_data,
        "dimensions": {"rows": max_row, "cols": max_col}
    }
    
    # Use AI to identify form fields
    cells_mapping = {}
    
    if quickPrompt and cell_data:
        try:
            cells_mapping = _ai_identify_form_fields(cell_data)
        except Exception as e:
            logger.warning(f"AI form field identification failed: {str(e)}")
            # Fallback: simple heuristic detection
            cells_mapping = _heuristic_form_fields(cell_data)
    else:
        cells_mapping = _heuristic_form_fields(cell_data)
    
    return {
        "cells": cells_mapping,
        "raw_structure": raw_structure
    }


def _ai_identify_form_fields(cell_data: List[Dict]) -> Dict:
    """
    Use AI to identify fillable form fields from cell data.
    
    Args:
        cell_data: List of cell information dicts
        
    Returns:
        Dict mapping field names to cell locations
    """
    logger.debug("Using AI to identify form fields...")
    
    system_prompt = """You are an expert at analyzing Excel form templates. 
Your task is to identify fillable fields in a form-style Excel template.
You must return ONLY valid JSON with no additional text or explanation."""

    user_prompt = f"""Analyze this Excel template structure and identify the fillable fields.
    
Template cells (cell address and current value):
{json.dumps(cell_data, indent=2)}

Identify fields where:
1. A cell contains a label (like "Customer Name:", "Date:", "Account #", etc.)
2. The adjacent cell (usually to the right or below) is where data should be entered

Return ONLY a JSON object in this exact format:
{{
    "fields": [
        {{
            "label": "Customer Name",
            "value_cell": "B2",
            "label_cell": "A2",
            "expected_type": "string"
        }}
    ]
}}

Rules:
- Include only actual fillable fields, not headers or titles
- expected_type should be: string, number, date, currency, or email
- If a cell appears to already have a value that looks like example data, still include it
- Return valid JSON only, no markdown or explanation"""

    try:
        response = quickPrompt(user_prompt, system=system_prompt, temp=0.0)
        logger.debug(f"AI response for form fields: {response[:500]}...")
        
        # Clean up response (remove markdown if present)
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r'^```json?\s*', '', response)
            response = re.sub(r'\s*```$', '', response)
        
        result = json.loads(response)
        
        # Convert to our expected format
        cells_mapping = {}
        for field in result.get("fields", []):
            label = field.get("label", "")
            if label:
                cells_mapping[label] = {
                    "cell": field.get("value_cell", ""),
                    "label_cell": field.get("label_cell", ""),
                    "expected_type": field.get("expected_type", "string")
                }
        
        logger.debug(f"AI identified {len(cells_mapping)} form fields")
        return cells_mapping
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"AI form field identification error: {str(e)}")
        raise


def _heuristic_form_fields(cell_data: List[Dict]) -> Dict:
    """
    Fallback heuristic to identify form fields without AI.
    
    Args:
        cell_data: List of cell information dicts
        
    Returns:
        Dict mapping field names to cell locations
    """
    logger.debug("Using heuristic form field detection...")
    
    cells_mapping = {}
    
    # Build a lookup by position
    cell_lookup = {(c['row'], c['col']): c for c in cell_data}
    
    for cell in cell_data:
        value = str(cell['value']).strip()
        row, col = cell['row'], cell['col']
        
        # Check if this looks like a label
        is_label = (
            value.endswith(':') or
            value.endswith('?') or
            any(keyword in value.lower() for keyword in 
                ['name', 'date', 'number', 'address', 'email', 'phone', 'id', 'amount', 'total'])
        )
        
        if is_label:
            # Check for value cell to the right
            right_cell = cell_lookup.get((row, col + 1))
            # Check for value cell below
            below_cell = cell_lookup.get((row + 1, col))
            
            label_name = value.rstrip(':').rstrip('?').strip()
            
            if right_cell or not below_cell:
                # Assume value is to the right
                value_cell = f"{get_column_letter(col + 1)}{row}"
            else:
                # Assume value is below
                value_cell = f"{get_column_letter(col)}{row + 1}"
            
            cells_mapping[label_name] = {
                "cell": value_cell,
                "label_cell": cell['cell'],
                "expected_type": "string"
            }
    
    logger.debug(f"Heuristic identified {len(cells_mapping)} form fields")
    return cells_mapping


def generate_schema_from_data(raw_data: Any, ai_instructions: str = None) -> Dict:
    """
    AI-generated schema mode: Analyze input data and create optimal schema.
    
    Args:
        raw_data: Input data to analyze
        ai_instructions: Hints about desired output structure
    
    Returns:
        Schema dict compatible with map_data_to_schema
    """
    logger.info("Generating schema from data using AI...")
    logger.debug(f"Input data type: {type(raw_data)}")
    logger.debug(f"Input data preview: {str(raw_data)[:500]}...")
    
    if not quickPrompt:
        raise RuntimeError("AI features unavailable - quickPrompt not imported")
    
    # Convert data to string if needed
    if isinstance(raw_data, (dict, list)):
        data_str = json.dumps(raw_data, indent=2, default=str)
    else:
        data_str = str(raw_data)
    
    system_prompt = """You are an expert at analyzing data and creating optimal Excel schemas.
Your task is to analyze input data and determine the best column structure for an Excel spreadsheet.
You must return ONLY valid JSON with no additional text or explanation."""

    user_prompt = f"""Analyze this data and create an optimal Excel schema for storing it.

INPUT DATA:
{data_str[:3000]}

{f"ADDITIONAL INSTRUCTIONS: {ai_instructions}" if ai_instructions else ""}

Create a schema that:
1. Captures all important data fields
2. Uses clear, professional column names
3. Identifies appropriate data types

Return ONLY a JSON object in this exact format:
{{
    "type": "table",
    "columns": [
        {{"name": "Column Name", "expected_type": "string"}},
        {{"name": "Amount", "expected_type": "currency"}},
        {{"name": "Date", "expected_type": "date"}}
    ],
    "data_start_row": 2
}}

Rules:
- Column names should be clear and professional
- expected_type should be: string, number, date, currency, email, or boolean
- Return valid JSON only, no markdown or explanation"""

    try:
        response = quickPrompt(user_prompt, system=system_prompt, temp=0.0)
        logger.debug(f"AI schema generation response: {response[:500]}...")
        
        # Clean up response
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r'^```json?\s*', '', response)
            response = re.sub(r'\s*```$', '', response)
        
        schema = json.loads(response)
        
        # Ensure required fields
        schema.setdefault("type", "table")
        schema.setdefault("data_start_row", 2)
        
        # Add column indices
        for i, col in enumerate(schema.get("columns", []), 1):
            col["column_index"] = i
            col["column_letter"] = get_column_letter(i)
        
        logger.info(f"Generated schema with {len(schema.get('columns', []))} columns")
        logger.debug(f"Generated schema: {json.dumps(schema, indent=2)}")
        
        return schema
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI schema response: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Schema generation error: {str(e)}", exc_info=True)
        raise


def map_data_to_schema(
    raw_data: Any,
    schema: Dict,
    ai_instructions: str = None,
    source_context: str = None
) -> Dict:
    """
    Use AI to map raw input data to the target schema.
    
    Args:
        raw_data: Input data (string, dict, list, or any extracted content)
        schema: Target schema from detect_template_schema or user-defined
        ai_instructions: Additional instructions for the AI
        source_context: Description of where the data came from
    
    Returns:
        For table type:
        {
            "rows": [
                {"Column A": "value1", "Column B": "value2"},
                ...
            ]
        }
        
        For form type:
        {
            "cells": {
                "B2": "Customer Name Value",
                "B3": "Account Number Value",
                ...
            }
        }
    """
    logger.info("Mapping data to schema using AI...")
    logger.debug(f"Schema type: {schema.get('type', 'unknown')}")
    logger.debug(f"Input data type: {type(raw_data)}")
    
    if not quickPrompt:
        raise RuntimeError("AI features unavailable - quickPrompt not imported")
    
    # Convert data to string if needed
    if isinstance(raw_data, (dict, list)):
        data_str = json.dumps(raw_data, indent=2, default=str)
    else:
        data_str = str(raw_data)
    
    template_type = schema.get("type", "table")
    
    if template_type == "table":
        return _map_data_to_table(data_str, schema, ai_instructions, source_context)
    else:
        return _map_data_to_form(data_str, schema, ai_instructions, source_context)


def _map_data_to_table(data_str: str, schema: Dict, ai_instructions: str, source_context: str) -> Dict:
    """Map data to table-style schema."""
    logger.debug("Mapping data to table schema...")
    
    columns = schema.get("columns", [])
    column_names = [c.get("name", c) if isinstance(c, dict) else c for c in columns]
    
    logger.debug(f"Target columns: {column_names}")
    
    system_prompt = sysprompts.WORKFLOW_EXCEL_TABLE_MAPPING_SYSTEM

    user_prompt = f"""Extract data from the source and map it to these Excel columns.

SOURCE DATA:
{data_str}

TARGET COLUMNS:
{json.dumps(column_names)}

MAPPING GUIDANCE:
- Use the field descriptions to understand what each source field contains
- Match source fields to target columns based on semantic meaning

{f"SOURCE CONTEXT: {source_context}" if source_context else ""}
{f"ADDITIONAL INSTRUCTIONS: {ai_instructions}" if ai_instructions else ""}

Rules:
1. Map semantically equivalent fields (e.g., "company" -> "Customer Name", "amt" -> "Amount")
2. Convert data types appropriately:
   - Dates: Use ISO format (YYYY-MM-DD) or readable format (Month DD, YYYY)
   - Currency: Include numeric value only (no $ symbol), or as formatted string
   - Numbers: Use numeric values where appropriate
3. If a column's data cannot be found in the source, use null
4. If the source contains multiple records, return multiple rows
5. If the source is unstructured text, extract all relevant information

Return ONLY a JSON object in this exact format:
{{
    "rows": [
        {{"Column Name 1": "value1", "Column Name 2": "value2"}},
        {{"Column Name 1": "value3", "Column Name 2": "value4"}}
    ],
    "warnings": ["Optional: any fields that couldn't be mapped"]
}}

Return valid JSON only, no markdown or explanation."""

    try:
        response = quickPrompt(user_prompt, system=system_prompt, temp=0.0)
        logger.debug(f"AI mapping response: {response[:500]}...")
        
        # Clean up response
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r'^```json?\s*', '', response)
            response = re.sub(r'\s*```$', '', response)
        
        result = json.loads(response)
        
        # Log any warnings from AI
        warnings = result.get("warnings", [])
        for warning in warnings:
            logger.warning(f"AI mapping warning: {warning}")
        
        rows = result.get("rows", [])
        logger.info(f"Mapped data to {len(rows)} row(s)")
        
        return {"rows": rows, "warnings": warnings}
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI mapping response: {str(e)}")
        logger.error(f"Raw response: {response}")
        raise
    except Exception as e:
        logger.error(f"Data mapping error: {str(e)}", exc_info=True)
        raise


def _map_data_to_form(data_str: str, schema: Dict, ai_instructions: str, source_context: str) -> Dict:
    """Map data to form-style schema."""
    logger.debug("Mapping data to form schema...")
    
    cells_schema = schema.get("cells", {})
    
    # Build field descriptions for AI
    field_descriptions = []
    for label, info in cells_schema.items():
        cell = info.get("cell", "") if isinstance(info, dict) else info
        expected_type = info.get("expected_type", "string") if isinstance(info, dict) else "string"
        field_descriptions.append({
            "label": label,
            "cell": cell,
            "expected_type": expected_type
        })
    
    logger.debug(f"Target fields: {[f['label'] for f in field_descriptions]}")
    
    system_prompt = sysprompts.WORKFLOW_EXCEL_FORM_MAPPING_SYSTEM

    user_prompt = f"""Extract data from the source and map it to these Excel form fields.

SOURCE DATA:
{data_str[:4000]}

TARGET FORM FIELDS:
{json.dumps(field_descriptions, indent=2)}

{f"SOURCE CONTEXT: {source_context}" if source_context else ""}
{f"ADDITIONAL INSTRUCTIONS: {ai_instructions}" if ai_instructions else ""}

Rules:
1. Map semantically equivalent fields from the source to target labels
2. Convert data types appropriately for each field's expected_type
3. If a field's data cannot be found, use null
4. Extract all relevant information from unstructured text

Return ONLY a JSON object in this exact format:
{{
    "cells": {{
        "B2": "value for cell B2",
        "B3": "value for cell B3"
    }},
    "field_mapping": {{
        "Field Label": "extracted value"
    }},
    "warnings": ["Optional: any fields that couldn't be mapped"]
}}

Return valid JSON only, no markdown or explanation."""

    try:
        response = quickPrompt(user_prompt, system=system_prompt, temp=0.0)
        logger.debug(f"AI form mapping response: {response[:500]}...")
        
        # Clean up response
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r'^```json?\s*', '', response)
            response = re.sub(r'\s*```$', '', response)
        
        result = json.loads(response)
        
        # Log any warnings from AI
        warnings = result.get("warnings", [])
        for warning in warnings:
            logger.warning(f"AI mapping warning: {warning}")
        
        cells = result.get("cells", {})
        
        # If cells not directly provided, build from field_mapping
        if not cells and result.get("field_mapping"):
            for label, value in result["field_mapping"].items():
                if label in cells_schema:
                    cell_info = cells_schema[label]
                    cell_addr = cell_info.get("cell", "") if isinstance(cell_info, dict) else cell_info
                    if cell_addr:
                        cells[cell_addr] = value
        
        logger.info(f"Mapped data to {len(cells)} cell(s)")
        
        return {"cells": cells, "warnings": warnings}
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI form mapping response: {str(e)}")
        logger.error(f"Raw response: {response}")
        raise
    except Exception as e:
        logger.error(f"Form mapping error: {str(e)}", exc_info=True)
        raise


def populate_excel(
    output_path: str,
    mapped_data: Dict,
    schema: Dict,
    template_path: str = None,
    operation: str = "append",  # "append", "overwrite", "new_from_template"
    cell_formatting: Dict[str, Dict] = None
) -> Dict:
    """
    Write mapped data to Excel file.
    
    Args:
        output_path: Where to save the result
        mapped_data: Data from map_data_to_schema
        schema: Schema used for mapping
        template_path: Source template (for new_from_template operation)
        operation: How to write the data
            - "append": Add rows to existing file (or create if not exists)
            - "overwrite": Replace all data rows (keep headers for table type)
            - "new_from_template": Create new file from template, then populate
        cell_formatting: Optional dict of AI-suggested cell formatting from extraction
    
    Returns:
        {
            "success": True,
            "file_path": "/path/to/output.xlsx",
            "rows_written": 5,           # for table type
            "cells_populated": 12,       # for form type
            "sheet_name": "Sheet1",
            "operation": "append"
        }
    """
    logger.info(f"Populating Excel file: {output_path}")
    logger.debug(f"Operation: {operation}")
    logger.debug(f"Template path: {template_path}")
    
    template_type = schema.get("type", "table")
    sheet_name = schema.get("sheet_name", "Sheet1")
    
    try:
        # Determine source workbook
        if operation == "new_from_template" and template_path:
            if not os.path.exists(template_path):
                raise FileNotFoundError(f"Template file not found: {template_path}")
            
            logger.debug(f"Creating new file from template: {template_path}")
            wb = load_workbook(template_path)
            
        elif os.path.exists(output_path):
            logger.debug(f"Opening existing file: {output_path}")
            wb = load_workbook(output_path)
            
        else:
            logger.debug("Creating new workbook")
            wb = Workbook()
            
            # If table type, add headers
            if template_type == "table":
                ws = wb.active
                ws.title = sheet_name
                columns = schema.get("columns", [])
                for col in columns:
                    col_name = col.get("name", col) if isinstance(col, dict) else col
                    col_idx = col.get("column_index", columns.index(col) + 1) if isinstance(col, dict) else columns.index(col) + 1
                    ws.cell(row=1, column=col_idx, value=col_name)
                logger.debug(f"Added headers: {[c.get('name', c) if isinstance(c, dict) else c for c in columns]}")
        
        # Get target worksheet
        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
        elif sheet_name:
            # Sheet name specified but doesn't exist yet — create it
            ws = wb.create_sheet(title=sheet_name)
            logger.debug(f"Created new sheet: {sheet_name}")
            # For table type, add headers to the new sheet
            if template_type == "table":
                columns = schema.get("columns", [])
                for col in columns:
                    col_name = col.get("name", col) if isinstance(col, dict) else col
                    col_idx = col.get("column_index", columns.index(col) + 1) if isinstance(col, dict) else columns.index(col) + 1
                    ws.cell(row=1, column=col_idx, value=col_name)
                logger.debug(f"Added headers to new sheet '{sheet_name}': {[c.get('name', c) if isinstance(c, dict) else c for c in columns]}")
        else:
            ws = wb.active
            if ws.title == "Sheet" and operation == "new_from_template":
                ws.title = sheet_name
        
        logger.debug(f"Working with sheet: {ws.title}")
        
        # Populate based on type
        start_row = None
        if template_type == "table":
            result, start_row = _populate_table(ws, mapped_data, schema, operation)
        else:
            result = _populate_form(ws, mapped_data, schema)

        # Apply AI-suggested cell formatting if provided
        formatting_result = None
        if cell_formatting and template_type == "table":
            try:
                data_start_row = start_row or 2
                rows_written = result.get("rows_written", 0)
                
                formatting_result = apply_cell_formatting(
                    ws=ws,
                    cell_formatting=cell_formatting,
                    schema=schema,
                    data_start_row=data_start_row,
                    rows_written=rows_written
                )
                logger.info(f"Cell formatting applied: {formatting_result}")
            except Exception as e:
                logger.error(f"Error applying cell formatting (data preserved): {str(e)}", exc_info=True)
                formatting_result = {"error": str(e), "cells_formatted": 0}
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.debug(f"Created output directory: {output_dir}")
        
        # Save workbook
        wb.save(output_path)
        wb.close()
        
        logger.info(f"Successfully saved Excel file: {output_path}")
        
        result.update({
            "success": True,
            "file_path": output_path,
            "sheet_name": ws.title,
            "operation": operation
        })

        # Include formatting results if formatting was applied
        if formatting_result:
            result["formatting"] = formatting_result
        
        return result
        
    except Exception as e:
        logger.error(f"Error populating Excel file: {str(e)}", exc_info=True)
        raise


def _populate_table(ws, mapped_data: Dict, schema: Dict, operation: str) -> Dict:
    """Populate table-style worksheet."""
    logger.debug("Populating table...")
    
    rows = mapped_data.get("rows", [])
    columns = schema.get("columns", [])
    
    if not rows:
        logger.warning("No rows to write")
        return {"rows_written": 0}
    
    # Build column name to index mapping
    col_mapping = {}
    for col in columns:
        col_name = col.get("name", col) if isinstance(col, dict) else col
        col_idx = col.get("column_index", columns.index(col) + 1) if isinstance(col, dict) else columns.index(col) + 1
        col_mapping[col_name] = col_idx
    
    logger.debug(f"Column mapping: {col_mapping}")
    
    # For append operation, check if any columns in our schema need headers written
    # This handles the case where new columns (like assumptions/sources) were added to schema
    if operation == "append":
        for col in columns:
            col_name = col.get("name", col) if isinstance(col, dict) else col
            col_idx = col.get("column_index", columns.index(col) + 1) if isinstance(col, dict) else columns.index(col) + 1
            
            # Check if header row has this column
            existing_header = ws.cell(row=1, column=col_idx).value
            if existing_header is None or str(existing_header).strip() == "":
                # Write the header for this new column
                ws.cell(row=1, column=col_idx, value=col_name)
                logger.info(f"Added new column header '{col_name}' at column {col_idx}")
    
    # Determine starting row
    if operation == "overwrite":
        start_row = schema.get("data_start_row", 2)
        # Clear existing data
        for row in range(start_row, ws.max_row + 1):
            for col in range(1, ws.max_column + 1):
                ws.cell(row=row, column=col, value=None)
        logger.debug(f"Cleared existing data from row {start_row}")
    else:  # append
        logger.debug(f"APPEND DEBUG: ws.max_row = {ws.max_row}")
        logger.debug(f"APPEND DEBUG: ws.title = {ws.title}")
        logger.debug(f"APPEND DEBUG: Row 2 Col 1 value = {ws.cell(row=2, column=1).value}")
        
        start_row = ws.max_row + 1
        logger.debug(f"APPEND DEBUG: Initial start_row (max_row + 1) = {start_row}")
        
        # If only header row exists, start at row 2
        if start_row == 2 and ws.cell(row=1, column=1).value is not None:
            start_row = 2
            logger.debug(f"APPEND DEBUG: Condition 1 matched - only headers exist")
        elif start_row == 1:
            start_row = 2
            logger.debug(f"APPEND DEBUG: Condition 2 matched - empty sheet")
        
        logger.debug(f"APPEND DEBUG: Final start_row = {start_row}")
    
    # Write rows
    rows_written = 0
    for row_data in rows:
        if not row_data:
            continue
            
        for col_name, value in row_data.items():
            if col_name in col_mapping:
                col_idx = col_mapping[col_name]
                ws.cell(row=start_row + rows_written, column=col_idx, value=value)
            else:
                logger.warning(f"Column '{col_name}' not found in schema, skipping")
        
        rows_written += 1
    
    logger.info(f"Wrote {rows_written} rows")
    
    return {"rows_written": rows_written}, start_row


def _populate_form(ws, mapped_data: Dict, schema: Dict) -> Dict:
    """Populate form-style worksheet."""
    logger.debug("Populating form...")
    
    cells = mapped_data.get("cells", {})
    
    if not cells:
        logger.warning("No cells to populate")
        return {"cells_populated": 0}
    
    cells_populated = 0
    
    for cell_addr, value in cells.items():
        # Skip None values AND empty strings to preserve existing data
        if value is None or (isinstance(value, str) and value.strip() == ''):
            logger.debug(f"Skipping cell {cell_addr} with empty/null value")
            continue
            
        try:
            ws[cell_addr] = value
            cells_populated += 1
            logger.debug(f"Set cell {cell_addr} = {str(value)[:50]}")
        except Exception as e:
            logger.warning(f"Failed to set cell {cell_addr}: {str(e)}")
    
    logger.info(f"Populated {cells_populated} cells")
    
    return {"cells_populated": cells_populated}


# Convenience function for common use case
def process_data_to_excel(
    raw_data: Any,
    output_path: str,
    template_path: str = None,
    schema_mode: str = "existing_template",  # or "ai_generated"
    ai_instructions: str = None,
    operation: str = "append",
    sheet_name: str = None
) -> Dict:
    """
    High-level convenience function to process data and write to Excel.
    
    This combines schema detection/generation, data mapping, and Excel population
    into a single call.
    
    Args:
        raw_data: Input data (any format)
        output_path: Where to save the Excel file
        template_path: Path to template (for existing_template mode)
        schema_mode: "existing_template" or "ai_generated"
        ai_instructions: Additional instructions for AI
        operation: "append", "overwrite", or "new_from_template"
        sheet_name: Target sheet name (optional)
    
    Returns:
        Result dict with success status and details
    """
    logger.info("=" * 60)
    logger.info("Starting Excel data processing")
    logger.info(f"Schema mode: {schema_mode}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Output: {output_path}")
    logger.info("=" * 60)
    
    try:
        # Step 1: Get or generate schema
        if schema_mode == "existing_template":
            if not template_path:
                raise ValueError("template_path required for existing_template mode")
            schema = detect_template_schema(template_path, sheet_name)
        else:  # ai_generated
            schema = generate_schema_from_data(raw_data, ai_instructions)
        
        logger.info(f"Schema ready. Type: {schema.get('type')}")
        
        # Step 2: Map data to schema
        mapped_data = map_data_to_schema(
            raw_data, 
            schema, 
            ai_instructions=ai_instructions
        )
        
        logger.info("Data mapping complete")
        
        # Step 3: Populate Excel
        result = populate_excel(
            output_path=output_path,
            mapped_data=mapped_data,
            schema=schema,
            template_path=template_path,
            operation=operation
        )
        
        logger.info("Excel population complete")
        logger.info(f"Result: {json.dumps(result, indent=2)}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in process_data_to_excel: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "file_path": output_path
        }


def extraction_result_to_dataframe(
    extraction_result: Dict,
    include_assumptions: bool = False,
    include_sources: bool = False,
    include_confidence: bool = False,
    transpose: bool = True
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Convert populate_schema_with_claude output to a pandas DataFrame.
    
    Args:
        extraction_result: Output from populate_schema_with_claude
        include_assumptions: Whether to include assumptions column(s)
        include_sources: Whether to include source pages column(s)
        include_confidence: Whether to include confidence level column(s) (LOW/MED/HIGH)
        transpose: If True, fields become columns (one row). 
                   If False, fields become rows (Field, Value, ... columns)
    
    Returns:
        Tuple of (DataFrame, list of warnings)
    """
    logger.info("Converting extraction result to DataFrame")
    logger.debug(f"Options - include_assumptions: {include_assumptions}, "
                 f"include_sources: {include_sources}, include_confidence: {include_confidence}, transpose: {transpose}")
    
    fields = extraction_result.get("fields", {})
    global_assumptions = extraction_result.get("global_assumptions", [])
    warnings = []
    
    if not fields:
        logger.warning("No fields found in extraction result")
        warnings.append("No fields found in extraction result")
        return pd.DataFrame(), warnings
    
    logger.debug(f"Processing {len(fields)} fields")
    
    if transpose:
        # One row with field names as columns
        row_data = {}
        assumptions_data = {}
        sources_data = {}
        confidence_data = {}
        
        for field_name, field_info in fields.items():
            value = field_info.get("value")
            row_data[field_name] = value
            
            if include_assumptions:
                assumptions = field_info.get("assumptions", [])
                assumptions_data[f"{field_name}_assumptions"] = "; ".join(assumptions) if assumptions else ""
            
            if include_sources:
                sources = field_info.get("sources", [])
                source_pages = []
                for src in sources:
                    pages = src.get("pages", [])
                    if pages:
                        source_pages.extend([str(p) for p in pages])
                sources_data[f"{field_name}_sources"] = ", ".join(source_pages) if source_pages else ""
            
            if include_confidence:
                confidence = field_info.get("confidence", "")
                confidence_data[f"{field_name}_confidence"] = confidence if confidence else ""
        
        # Combine all data
        combined_data = {**row_data}
        if include_assumptions:
            combined_data.update(assumptions_data)
        if include_sources:
            combined_data.update(sources_data)
        if include_confidence:
            combined_data.update(confidence_data)
        
        df = pd.DataFrame([combined_data])
        
    else:
        # Multiple rows with Field, Value, Assumptions, Sources, Confidence columns
        rows = []
        for field_name, field_info in fields.items():
            row = {
                "Field": field_name,
                "Value": field_info.get("value")
            }
            
            if include_assumptions:
                assumptions = field_info.get("assumptions", [])
                row["Assumptions"] = "; ".join(assumptions) if assumptions else ""
            
            if include_sources:
                sources = field_info.get("sources", [])
                source_pages = []
                source_notes = []
                for src in sources:
                    pages = src.get("pages", [])
                    if pages:
                        source_pages.extend([str(p) for p in pages])
                    note = src.get("notes")
                    if note:
                        source_notes.append(note)
                
                row["Source Pages"] = ", ".join(source_pages) if source_pages else ""
                row["Source Notes"] = "; ".join(source_notes) if source_notes else ""
            
            if include_confidence:
                confidence = field_info.get("confidence", "")
                row["Confidence"] = confidence if confidence else ""
        
            rows.append(row)
        
        df = pd.DataFrame(rows)
    
    # Add global assumptions as a warning/note
    if global_assumptions:
        warnings.append(f"Global assumptions: {'; '.join(global_assumptions)}")
    
    logger.info(f"Created DataFrame with shape {df.shape}")
    logger.debug(f"Columns: {list(df.columns)}")
    
    return df, warnings


def apply_cell_formatting(
    ws,
    cell_formatting: Dict[str, Dict],
    schema: Dict,
    data_start_row: int,
    rows_written: int
) -> Dict:
    """
    Apply AI-suggested cell-level formatting to a worksheet.
    
    This applies formatting based on the cell_formatting dict returned by
    the AI extraction, which contains per-field formatting suggestions.
    
    Args:
        ws: openpyxl worksheet object
        cell_formatting: Dict from AI extraction result, e.g.:
            {
                "amount": {"fill": "#FFCDD2", "font_color": "#B71C1C", "bold": true, "reason": "..."},
                "status": {"fill": "#C8E6C9", "reason": "..."}
            }
        schema: The schema used for writing (contains column info)
        data_start_row: Row where data starts (typically 2)
        rows_written: Number of data rows written
        
    Returns:
        Dict with results: {"cells_formatted": int, "errors": []}
    """
    if not cell_formatting:
        logger.debug("No cell formatting to apply")
        return {"cells_formatted": 0, "errors": []}
    
    logger.info(f"Applying AI-suggested cell formatting for {len(cell_formatting)} field(s)")
    
    results = {
        "cells_formatted": 0,
        "errors": []
    }
    
    # Build column name to index mapping from schema
    columns = schema.get("columns", [])
    col_mapping = {}
    for col in columns:
        col_name = col.get("name", col) if isinstance(col, dict) else col
        col_idx = col.get("column_index", columns.index(col) + 1) if isinstance(col, dict) else columns.index(col) + 1
        col_mapping[col_name] = col_idx
    
    logger.debug(f"Column mapping for formatting: {col_mapping}")
    
    for field_name, format_spec in cell_formatting.items():
        try:
            if field_name not in col_mapping:
                logger.warning(f"Field '{field_name}' not found in columns, skipping formatting")
                continue
            
            col_idx = col_mapping[field_name]
            
            # Parse formatting spec
            fill = None
            font_kwargs = {}
            
            # Handle fill/background color
            fill_color = format_spec.get("fill") or format_spec.get("background")
            if fill_color:
                # Strip # if present
                fill_color = fill_color.lstrip("#")
                try:
                    fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
                except Exception as e:
                    logger.warning(f"Invalid fill color '{fill_color}' for field '{field_name}': {e}")
            
            # Handle font color
            font_color = format_spec.get("font_color") or format_spec.get("color")
            if font_color:
                font_color = font_color.lstrip("#")
                font_kwargs["color"] = font_color
            
            # Handle bold
            if format_spec.get("bold"):
                font_kwargs["bold"] = True
            
            # Handle italic
            if format_spec.get("italic"):
                font_kwargs["italic"] = True
            
            font = Font(**font_kwargs) if font_kwargs else None
            
            # Apply formatting to all data rows in this column
            for row in range(data_start_row, data_start_row + rows_written):
                cell = ws.cell(row=row, column=col_idx)
                if fill:
                    cell.fill = fill
                if font:
                    cell.font = font
                results["cells_formatted"] += 1
            
            reason = format_spec.get("reason", "")
            logger.debug(f"Formatted field '{field_name}': fill={fill_color}, font_color={font_color}, "
                        f"bold={format_spec.get('bold')}, reason={reason}")
            
        except Exception as e:
            error_msg = f"Failed to format field '{field_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            results["errors"].append(error_msg)
    
    logger.info(f"Cell formatting complete: {results['cells_formatted']} cells formatted")
    return results

def write_extraction_to_excel(
    extraction_result: Dict,
    output_path: str,
    template_path: str = None,
    operation: str = "new",  # "new", "append", "new_from_template"
    include_assumptions: bool = False,
    include_sources: bool = False,
    include_confidence: bool = False,
    sheet_name: str = None,
    field_mapping: Dict[str, str] = None,
    ai_mapping_instructions: str = None,
    field_definitions: List[Dict] = None 
) -> Dict:
    """
    Write extraction results to an Excel file.
    
    Args:
        extraction_result: Output from populate_schema_with_claude
        output_path: Where to save the Excel file
        template_path: Path to template (for new_from_template or append operations)
        operation: 
            - "new": Create new file with auto-generated columns
            - "append": Append to existing file
            - "new_from_template": Create new file from template, then populate
        include_assumptions: Include assumptions columns
        include_sources: Include source pages columns
        include_confidence: Include confidence level columns (LOW/MED/HIGH)
        sheet_name: Target sheet name (optional)
        field_mapping: Manual field->column mapping, e.g. {"customer_name": "Retailer Name"}
        ai_mapping_instructions: Instructions for AI to map fields to template columns
    
    Returns:
        Result dict with success status and details
    """
    logger.info("=" * 60)
    logger.info("Writing extraction result to Excel")
    logger.info(f"Output path: {output_path}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Template: {template_path}")
    logger.info("=" * 60)
    
    try:
        # Convert extraction result to simple values dict for mapping
        fields = extraction_result.get("fields", {})
        values_dict = {k: v.get("value") for k, v in fields.items()}
        
        logger.debug(f"Extracted values: {values_dict}")

        # Extract AI-suggested cell formatting if present
        cell_formatting = extraction_result.get("cell_formatting", {})
        if cell_formatting:
            logger.info(f"AI provided cell formatting for {len(cell_formatting)} field(s)")
            logger.debug(f"Cell formatting details: {cell_formatting}")

        # Transform cell_formatting keys using field_mapping if provided
        # The AI returns formatting with extraction field names (e.g., "customer")
        # but Excel columns may have different names (e.g., "CUSTOMER")
        if cell_formatting and field_mapping:
            transformed_formatting = {}
            for field_name, format_spec in cell_formatting.items():
                # Look up the mapped column name
                mapped_name = field_mapping.get(field_name)
                if mapped_name:
                    transformed_formatting[mapped_name] = format_spec
                    logger.debug(f"Transformed formatting key '{field_name}' -> '{mapped_name}'")
                else:
                    # Keep original name if no mapping exists
                    transformed_formatting[field_name] = format_spec
            cell_formatting = transformed_formatting
            logger.debug(f"Transformed cell_formatting keys using field_mapping")
        
        # Determine if we need to map to an existing schema
        use_template_schema = operation in ["append", "new_from_template"] and template_path

        # For append without template, read schema from existing output file
        if operation == "append" and not template_path:
            if os.path.exists(output_path):
                use_template_schema = True
                template_path = output_path  # Use existing file as the schema source
            else:
                return {
                    "success": False,
                    "error": f"Cannot append - file does not exist: {output_path}",
                    "file_path": output_path
                }
        
        if use_template_schema:
            logger.info("Using template schema for mapping")
            
            # Detect template schema
            template_schema = detect_template_schema(template_path, sheet_name)
            logger.debug(f"Template schema type: {template_schema.get('type')}")
            
            if template_schema.get("type") == "table":
                # Get template columns
                template_columns = [
                    c.get("name", c) if isinstance(c, dict) else c 
                    for c in template_schema.get("columns", [])
                ]
                logger.debug(f"Template columns: {template_columns}")
                
                # Build case-insensitive lookup: lowercase -> actual template column name
                template_columns_lower = {col.lower(): col for col in template_columns}
                logger.debug(f"Template columns (case-insensitive lookup): {template_columns_lower}")
                
                # Track columns that need to be created (not in template)
                columns_to_create = []
                
                # Find the maximum existing column index for adding new columns
                max_col_idx = max(
                    (c.get("column_index", i+1) if isinstance(c, dict) else i+1)
                    for i, c in enumerate(template_schema.get("columns", []))
                ) if template_schema.get("columns") else 0
                
                # Build set of existing column names for quick lookup (will be updated as we add columns)
                existing_col_names = set(template_columns)
                existing_col_names_lower = set(template_columns_lower.keys())
                
                # Normalize field_mapping values to match actual template column names
                # This handles case mismatches between user's mapping and template columns
                normalized_field_mapping = None
                if field_mapping:
                    normalized_field_mapping = {}
                    for extract_field, mapped_col in field_mapping.items():
                        mapped_col_lower = mapped_col.lower()
                        if mapped_col_lower in template_columns_lower:
                            # Use the actual template column name (preserves original case)
                            actual_col_name = template_columns_lower[mapped_col_lower]
                            normalized_field_mapping[extract_field] = actual_col_name
                            if actual_col_name != mapped_col:
                                logger.info(f"Normalized field mapping: '{extract_field}' -> '{mapped_col}' -> '{actual_col_name}'")
                        else:
                            # Column doesn't exist - will create it with the user's specified name
                            normalized_field_mapping[extract_field] = mapped_col
                            
                            # Check if we've already queued this column for creation (case-insensitive)
                            if mapped_col_lower not in existing_col_names_lower:
                                max_col_idx += 1
                                columns_to_create.append({
                                    "name": mapped_col,
                                    "column_index": max_col_idx,
                                    "column_letter": get_column_letter(max_col_idx)
                                })
                                existing_col_names.add(mapped_col)
                                existing_col_names_lower.add(mapped_col_lower)
                                template_columns_lower[mapped_col_lower] = mapped_col
                                logger.info(f"Will create new column '{mapped_col}' at index {max_col_idx} for field '{extract_field}'")
                    
                    logger.debug(f"Normalized field mapping: {normalized_field_mapping}")
                
                # Map extraction fields to template columns
                if normalized_field_mapping:
                    # Use normalized manual mapping
                    logger.info("Using manual field mapping (normalized)")
                    mapped_row = {}
                    for field_name, value in values_dict.items():
                        if field_name in normalized_field_mapping:
                            mapped_row[normalized_field_mapping[field_name]] = value
                        elif field_name in template_columns:
                            mapped_row[field_name] = value
                        elif field_name.lower() in template_columns_lower:
                            # Case-insensitive fallback for direct field names
                            mapped_row[template_columns_lower[field_name.lower()]] = value
                        else:
                            # Field not in mapping and not in template - create column with field name
                            field_name_lower = field_name.lower()
                            if field_name_lower not in existing_col_names_lower:
                                max_col_idx += 1
                                columns_to_create.append({
                                    "name": field_name,
                                    "column_index": max_col_idx,
                                    "column_letter": get_column_letter(max_col_idx)
                                })
                                existing_col_names.add(field_name)
                                existing_col_names_lower.add(field_name_lower)
                                template_columns_lower[field_name_lower] = field_name
                                logger.info(f"Will create new column '{field_name}' at index {max_col_idx} (unmapped field)")
                            mapped_row[field_name] = value
                    
                    # Update field_mapping reference to use normalized version for metadata columns
                    field_mapping = normalized_field_mapping
                    
                else:
                    # Use AI mapping
                    logger.info("Using AI to map fields to template columns")
                    mapping_instructions = ai_mapping_instructions or \
                        "Map the extracted field names to the template column names semantically."
                    
                    # Build enhanced context with field descriptions
                    if field_definitions:
                        field_context = {
                            fd.get('name'): {
                                'value': values_dict.get(fd.get('name')),
                                'description': fd.get('description', '')
                            }
                            for fd in field_definitions
                            if fd.get('name') in values_dict
                        }
                    else:
                        field_context = values_dict
                    
                    mapped_data = map_data_to_schema(
                        raw_data=field_context,
                        schema=template_schema,
                        ai_instructions=mapping_instructions
                    )
                    
                    if mapped_data.get("rows"):
                        mapped_row = mapped_data["rows"][0]
                        
                        # Infer field_mapping from AI result by matching values
                        # This allows metadata columns to use consistent naming with mapped columns
                        inferred_mapping = {}
                        for extract_field, extract_value in values_dict.items():
                            for col_name, col_value in mapped_row.items():
                                # Match by value (handle type differences)
                                if str(extract_value) == str(col_value) or extract_value == col_value:
                                    inferred_mapping[extract_field] = col_name
                                    break
                        
                        if inferred_mapping:
                            field_mapping = inferred_mapping
                            logger.info(f"Inferred field mapping from AI result: {field_mapping}")
                        else:
                            logger.debug("Could not infer field mapping from AI result - using original field names for metadata")
                    else:
                        mapped_row = {}
                        logger.warning("AI mapping returned no rows")
                
                # Add any new columns to the schema (before metadata columns)
                if columns_to_create:
                    template_schema["columns"] = template_schema.get("columns", []) + columns_to_create
                    logger.info(f"Extended schema with {len(columns_to_create)} new column(s): {[c['name'] for c in columns_to_create]}")
                
                # Add assumptions, sources, and confidence if requested
                logger.info(f"Include assumptions: {include_assumptions}, Include sources: {include_sources}, Include confidence: {include_confidence}")
                
                if include_assumptions or include_sources or include_confidence:
                    # Use the existing_col_names and max_col_idx we've been maintaining
                    # (they include any new columns we created above)
                    logger.debug(f"Current column names for metadata check: {existing_col_names}")
                    
                    # Track new metadata columns to add to schema
                    new_columns = []
                    
                    for field_name, field_info in fields.items():
                        # Find the mapped column name
                        col_name = field_mapping.get(field_name, field_name) if field_mapping else field_name
                        
                        if include_assumptions:
                            assumptions = field_info.get("assumptions", [])
                            assumptions_col_name = f"{col_name}_assumptions"
                            assumptions_col_name_lower = assumptions_col_name.lower()
                            if assumptions:
                                mapped_row[assumptions_col_name] = "; ".join(assumptions)
                            
                            # Add column to schema if it doesn't exist (case-insensitive check)
                            if assumptions_col_name_lower not in existing_col_names_lower:
                                max_col_idx += 1
                                new_columns.append({
                                    "name": assumptions_col_name,
                                    "column_index": max_col_idx,
                                    "column_letter": get_column_letter(max_col_idx)
                                })
                                existing_col_names.add(assumptions_col_name)
                                existing_col_names_lower.add(assumptions_col_name_lower)
                                logger.debug(f"Added new schema column: {assumptions_col_name} at index {max_col_idx}")
                        
                        if include_sources:
                            sources = field_info.get("sources", [])
                            source_pages = []
                            for src in sources:
                                pages = src.get("pages", [])
                                source_pages.extend([str(p) for p in pages])
                            
                            sources_col_name = f"{col_name}_sources"
                            sources_col_name_lower = sources_col_name.lower()
                            if source_pages:
                                mapped_row[sources_col_name] = ", ".join(source_pages)
                            
                            # Add column to schema if it doesn't exist (case-insensitive check)
                            if sources_col_name_lower not in existing_col_names_lower:
                                max_col_idx += 1
                                new_columns.append({
                                    "name": sources_col_name,
                                    "column_index": max_col_idx,
                                    "column_letter": get_column_letter(max_col_idx)
                                })
                                existing_col_names.add(sources_col_name)
                                existing_col_names_lower.add(sources_col_name_lower)
                                logger.debug(f"Added new schema column: {sources_col_name} at index {max_col_idx}")
                        
                        if include_confidence:
                            confidence = field_info.get("confidence", "")
                            confidence_col_name = f"{col_name}_confidence"
                            confidence_col_name_lower = confidence_col_name.lower()
                            if confidence:
                                mapped_row[confidence_col_name] = confidence
                            
                            # Add column to schema if it doesn't exist (case-insensitive check)
                            if confidence_col_name_lower not in existing_col_names_lower:
                                max_col_idx += 1
                                new_columns.append({
                                    "name": confidence_col_name,
                                    "column_index": max_col_idx,
                                    "column_letter": get_column_letter(max_col_idx)
                                })
                                existing_col_names.add(confidence_col_name)
                                existing_col_names_lower.add(confidence_col_name_lower)
                                logger.debug(f"Added new schema column: {confidence_col_name} at index {max_col_idx}")
                    
                    # Extend template schema with new columns
                    if new_columns:
                        template_schema["columns"] = template_schema.get("columns", []) + new_columns
                        logger.info(f"Extended schema with {len(new_columns)} new assumption/source/confidence column(s)")
                
                # Prepare data for populate_excel
                mapped_data_final = {"rows": [mapped_row]}
                
            else:
                # Form-style template - use AI mapping for cells
                logger.info("Template is form-style, using AI cell mapping")
                mapped_data_final = map_data_to_schema(
                    raw_data=values_dict,
                    schema=template_schema,
                    ai_instructions=ai_mapping_instructions
                )
            
            # Write to Excel
            result = populate_excel(
                output_path=output_path,
                mapped_data=mapped_data_final,
                schema=template_schema,
                template_path=template_path,
                operation="append" if operation == "append" else "new_from_template",
                cell_formatting=cell_formatting
            )
            
        else:
            # New file without template - use extraction fields as columns
            logger.info("Creating new file from extraction fields")
            
            # Convert to DataFrame
            df, warnings = extraction_result_to_dataframe(
                extraction_result,
                include_assumptions=include_assumptions,
                include_sources=include_sources,
                include_confidence=include_confidence,
                transpose=True  # Fields as columns
            )
            
            # Build schema from DataFrame columns
            schema = {
                "type": "table",
                "columns": [{"name": col, "column_index": i+1, "column_letter": get_column_letter(i+1)}
                           for i, col in enumerate(df.columns)],
                "data_start_row": 2
            }
            # Preserve sheet_name so populate_excel uses the correct sheet
            if sheet_name:
                schema["sheet_name"] = sheet_name

            # Convert DataFrame row to dict
            if len(df) > 0:
                row_data = df.iloc[0].to_dict()
                mapped_data_final = {"rows": [row_data]}
            else:
                mapped_data_final = {"rows": []}
            
            # Write to Excel
            result = populate_excel(
                output_path=output_path,
                mapped_data=mapped_data_final,
                schema=schema,
                template_path=None,
                operation="append" if operation == "append" else "overwrite",
                cell_formatting=cell_formatting
            )
            
            # Add warnings
            if warnings:
                result["warnings"] = result.get("warnings", []) + warnings
        
        logger.info(f"Excel write complete: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Error writing extraction to Excel: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "file_path": output_path
        }
