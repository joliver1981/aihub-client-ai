"""
Self-Correction Engine
========================
When a plan step fails, this engine attempts automatic correction
before escalating to the user. Dispatches to strategy-specific handlers
based on the failure analysis.

Correction flow per step:
  1. Step executes and fails
  2. FailureAnalyzer classifies the failure
  3. SelfCorrectionEngine tries the suggested correction strategy
  4. If correction succeeds: step re-executes with corrected parameters
  5. If correction fails or retries exhausted: escalate to user
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .failure_analyzer import FailureAnalysis, FailureCategory

logger = logging.getLogger(__name__)


class CorrectionStrategy(str, Enum):
    RETRY_WITH_DEFAULTS = "retry_with_defaults"
    PARAMETER_CORRECTION = "parameter_correction"
    RETRY_WITH_ENRICHMENT = "retry_with_enrichment"
    CREATE_PREREQUISITE = "create_prerequisite"
    ASK_USER = "ask_user"
    SKIP_AND_CONTINUE = "skip_and_continue"


@dataclass
class CorrectionResult:
    """Result of a correction attempt."""
    strategy: CorrectionStrategy
    success: bool
    new_parameters: Optional[dict] = None
    user_question: Optional[str] = None
    message: str = ""


class SelfCorrectionEngine:
    """
    Attempts automatic corrections for failed execution steps.
    Uses the existing ActionExecutor for re-execution and parameter
    enrichment infrastructure from nodes.py.
    """

    MAX_CORRECTIONS_PER_STEP = 2

    def __init__(self, action_registry=None):
        """
        Args:
            action_registry: The action registry for looking up action definitions.
                             If None, will be loaded lazily.
        """
        self._action_registry = action_registry

    def _get_action_registry(self):
        if self._action_registry is None:
            try:
                from execution import get_action_registry
                self._action_registry = get_action_registry()
            except Exception:
                pass
        return self._action_registry

    async def attempt_correction(
        self,
        step: dict,
        failure_analysis: FailureAnalysis,
        system_context: Optional[Any],
        attempt_number: int,
    ) -> CorrectionResult:
        """
        Attempt to correct a failed step based on the failure analysis.

        Args:
            step: The plan step dict that failed
            failure_analysis: The analyzed failure with category and strategies
            system_context: Optional SystemContext for resource validation
            attempt_number: Which correction attempt this is (1-based)

        Returns:
            CorrectionResult indicating what happened and what to do next
        """
        if attempt_number > self.MAX_CORRECTIONS_PER_STEP:
            return CorrectionResult(
                strategy=CorrectionStrategy.ASK_USER,
                success=False,
                message=f"Max correction attempts ({self.MAX_CORRECTIONS_PER_STEP}) reached",
            )

        # Pick the best strategy based on the failure analysis
        strategies = failure_analysis.suggested_strategies
        if not strategies:
            strategies = ["ask_user"]

        # Try strategies in order
        for strategy_name in strategies:
            try:
                strategy = CorrectionStrategy(strategy_name)
            except ValueError:
                continue

            logger.info(
                f"[self_correction] Attempting {strategy.value} for "
                f"{step.get('domain')}.{step.get('action')} (attempt {attempt_number})"
            )

            handler = self._get_handler(strategy)
            if handler is None:
                continue

            result = await handler(step, failure_analysis, system_context)
            if result.success or result.user_question:
                return result

        # All strategies failed — formulate a question for the user
        return await self._formulate_user_question(step, failure_analysis, system_context)

    def _get_handler(self, strategy: CorrectionStrategy):
        """Get the handler method for a strategy."""
        handlers = {
            CorrectionStrategy.RETRY_WITH_DEFAULTS: self._retry_with_defaults,
            CorrectionStrategy.PARAMETER_CORRECTION: self._correct_parameters,
            CorrectionStrategy.RETRY_WITH_ENRICHMENT: self._retry_with_enrichment,
            CorrectionStrategy.CREATE_PREREQUISITE: self._create_prerequisite,
            CorrectionStrategy.ASK_USER: self._formulate_user_question,
        }
        return handlers.get(strategy)

    async def _retry_with_defaults(
        self,
        step: dict,
        analysis: FailureAnalysis,
        system_context: Optional[Any],
    ) -> CorrectionResult:
        """Fill missing required fields with sensible defaults from the action definition."""
        domain = step.get("domain", "")
        action = step.get("action", "")
        capability_id = f"{domain}.{action}"
        parameters = dict(step.get("parameters", {}))

        registry = self._get_action_registry()
        if not registry:
            return CorrectionResult(
                strategy=CorrectionStrategy.RETRY_WITH_DEFAULTS,
                success=False,
                message="Action registry not available",
            )

        action_def = registry.get_action(capability_id)
        if not action_def or not action_def.is_simple:
            return CorrectionResult(
                strategy=CorrectionStrategy.RETRY_WITH_DEFAULTS,
                success=False,
                message=f"No action definition found for {capability_id}",
            )

        # Fill defaults for missing required fields
        changed = False
        for field_def in action_def.primary_route.input_fields:
            if field_def.name not in parameters:
                if field_def.default is not None:
                    parameters[field_def.name] = field_def.default
                    changed = True
                    logger.info(f"[self_correction] Added default: {field_def.name}={field_def.default}")
                elif field_def.required and field_def.field_type.value == "boolean":
                    parameters[field_def.name] = True
                    changed = True
                    logger.info(f"[self_correction] Added boolean default: {field_def.name}=True")

        if not changed:
            return CorrectionResult(
                strategy=CorrectionStrategy.RETRY_WITH_DEFAULTS,
                success=False,
                message="No defaults available to fill",
            )

        return CorrectionResult(
            strategy=CorrectionStrategy.RETRY_WITH_DEFAULTS,
            success=True,
            new_parameters=parameters,
            message=f"Applied defaults for missing fields",
        )

    async def _correct_parameters(
        self,
        step: dict,
        analysis: FailureAnalysis,
        system_context: Optional[Any],
    ) -> CorrectionResult:
        """
        Fix specific parameter values based on the error message.
        Re-runs AI parameter extraction with the error as additional context.
        """
        domain = step.get("domain", "")
        action = step.get("action", "")
        capability_id = f"{domain}.{action}"
        description = step.get("description", "")
        parameters = dict(step.get("parameters", {}))

        registry = self._get_action_registry()
        if not registry:
            return CorrectionResult(
                strategy=CorrectionStrategy.PARAMETER_CORRECTION,
                success=False,
                message="Action registry not available",
            )

        action_def = registry.get_action(capability_id)
        if not action_def or not action_def.is_simple:
            return CorrectionResult(
                strategy=CorrectionStrategy.PARAMETER_CORRECTION,
                success=False,
                message=f"No action definition found for {capability_id}",
            )

        # If we know which field is wrong, remove it so enrichment re-generates it
        if analysis.error_field and analysis.error_field in parameters:
            logger.info(f"[self_correction] Removing bad field '{analysis.error_field}' for re-extraction")
            del parameters[analysis.error_field]

        # Build an enriched description that includes the error context
        error_context = analysis.error_details or analysis.root_cause
        enriched_description = (
            f"{description}\n\n"
            f"IMPORTANT: The previous attempt failed with this error: {error_context}\n"
            f"Please correct the parameters accordingly."
        )

        # Re-run parameter extraction with the error context
        try:
            from builder_config import get_llm
            from langchain_core.messages import SystemMessage, HumanMessage

            # Build the prompt with action field definitions
            required_fields = []
            optional_fields = []
            for f in action_def.primary_route.input_fields:
                field_info = f"{f.name} ({f.field_type.value})"
                if f.description:
                    field_info += f" - {f.description}"
                if f.choices:
                    field_info += f" [choices: {', '.join(f.choices)}]"
                if f.default is not None:
                    field_info += f" [default: {f.default}]"

                if f.required and f.default is None:
                    required_fields.append(field_info)
                else:
                    optional_fields.append(field_info)

            prompt = f"""Extract CORRECTED parameter values for this API call.

Capability: {capability_id}
Description: {enriched_description}

Required fields:
{chr(10).join(f'  - {f}' for f in required_fields) or '  (none)'}

Optional fields:
{chr(10).join(f'  - {f}' for f in optional_fields) or '  (none)'}

Current parameters (these had errors, fix them):
{json.dumps(parameters, indent=2)}

The error was: {error_context}

Return ONLY corrected JSON — no explanation."""

            llm = get_llm(mini=True, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content="You fix API call parameters based on error messages. Return only valid JSON."),
                HumanMessage(content=prompt),
            ])

            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

            corrected = json.loads(raw)
            if isinstance(corrected, dict):
                # Filter to valid fields only
                valid_fields = {f.name for f in action_def.primary_route.input_fields}
                corrected = {k: v for k, v in corrected.items() if k in valid_fields}

                if corrected != parameters:
                    logger.info(f"[self_correction] Parameters corrected: {corrected}")
                    return CorrectionResult(
                        strategy=CorrectionStrategy.PARAMETER_CORRECTION,
                        success=True,
                        new_parameters=corrected,
                        message="Parameters corrected based on error analysis",
                    )

        except Exception as e:
            logger.warning(f"[self_correction] Parameter correction failed: {e}")

        return CorrectionResult(
            strategy=CorrectionStrategy.PARAMETER_CORRECTION,
            success=False,
            message="Could not correct parameters",
        )

    async def _retry_with_enrichment(
        self,
        step: dict,
        analysis: FailureAnalysis,
        system_context: Optional[Any],
    ) -> CorrectionResult:
        """Re-run parameter enrichment from scratch with error context."""
        # This delegates to _correct_parameters which already includes
        # error context in the enrichment. The distinction is that
        # _retry_with_enrichment removes ALL parameters and re-extracts,
        # while _correct_parameters only removes the bad field.
        domain = step.get("domain", "")
        action = step.get("action", "")
        capability_id = f"{domain}.{action}"
        description = step.get("description", "")

        registry = self._get_action_registry()
        if not registry:
            return CorrectionResult(
                strategy=CorrectionStrategy.RETRY_WITH_ENRICHMENT,
                success=False,
                message="Action registry not available",
            )

        action_def = registry.get_action(capability_id)
        if not action_def or not action_def.is_simple:
            return CorrectionResult(
                strategy=CorrectionStrategy.RETRY_WITH_ENRICHMENT,
                success=False,
                message=f"No action definition found for {capability_id}",
            )

        # Start fresh — re-extract all parameters
        error_context = analysis.error_details or analysis.root_cause
        enriched_description = (
            f"{description}\n\n"
            f"IMPORTANT: A previous attempt with different parameters failed: {error_context}\n"
            f"Generate completely new parameter values."
        )

        try:
            from builder_config import get_llm
            from langchain_core.messages import SystemMessage, HumanMessage

            required_fields = []
            optional_fields = []
            for f in action_def.primary_route.input_fields:
                field_info = f"{f.name} ({f.field_type.value})"
                if f.description:
                    field_info += f" - {f.description}"
                if f.choices:
                    field_info += f" [choices: {', '.join(f.choices)}]"
                if f.default is not None:
                    field_info += f" [default: {f.default}]"
                if f.required and f.default is None:
                    required_fields.append(field_info)
                else:
                    optional_fields.append(field_info)

            prompt = f"""Extract parameter values for this API call from scratch.

Capability: {capability_id}
Description: {enriched_description}

Required fields:
{chr(10).join(f'  - {f}' for f in required_fields) or '  (none)'}

Optional fields:
{chr(10).join(f'  - {f}' for f in optional_fields) or '  (none)'}

Return ONLY valid JSON — no explanation."""

            llm = get_llm(mini=True, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content="You extract API parameters from descriptions. Return only valid JSON."),
                HumanMessage(content=prompt),
            ])

            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

            extracted = json.loads(raw)
            if isinstance(extracted, dict):
                valid_fields = {f.name for f in action_def.primary_route.input_fields}
                extracted = {k: v for k, v in extracted.items() if k in valid_fields}

                logger.info(f"[self_correction] Re-extracted parameters: {extracted}")
                return CorrectionResult(
                    strategy=CorrectionStrategy.RETRY_WITH_ENRICHMENT,
                    success=True,
                    new_parameters=extracted,
                    message="Parameters re-extracted from scratch with error context",
                )

        except Exception as e:
            logger.warning(f"[self_correction] Enrichment retry failed: {e}")

        return CorrectionResult(
            strategy=CorrectionStrategy.RETRY_WITH_ENRICHMENT,
            success=False,
            message="Could not re-extract parameters",
        )

    async def _create_prerequisite(
        self,
        step: dict,
        analysis: FailureAnalysis,
        system_context: Optional[Any],
    ) -> CorrectionResult:
        """
        When a step fails because a referenced resource doesn't exist,
        suggest creating it as a prerequisite. This doesn't auto-create
        (that's Phase 4) — it formulates a specific question.
        """
        root_cause = analysis.root_cause or ""
        capability_id = f"{step.get('domain', '')}.{step.get('action', '')}"

        # For now, this surfaces the missing resource as a user question
        # Phase 4 (adaptive config) will add actual auto-creation
        if "connection" in root_cause.lower():
            return CorrectionResult(
                strategy=CorrectionStrategy.CREATE_PREREQUISITE,
                success=False,
                user_question=(
                    f"This step needs a database connection that doesn't exist yet. "
                    f"Would you like me to create a database connection first? "
                    f"I'll need the connection details (server, database name, credentials)."
                ),
                message="Missing prerequisite: database connection",
            )

        if "agent" in root_cause.lower() and "not found" in root_cause.lower():
            return CorrectionResult(
                strategy=CorrectionStrategy.CREATE_PREREQUISITE,
                success=False,
                user_question=(
                    f"The agent referenced in this step doesn't exist. "
                    f"Would you like me to create it first?"
                ),
                message="Missing prerequisite: agent",
            )

        if "workflow" in root_cause.lower() and "not found" in root_cause.lower():
            return CorrectionResult(
                strategy=CorrectionStrategy.CREATE_PREREQUISITE,
                success=False,
                user_question=(
                    f"The workflow referenced in this step doesn't exist. "
                    f"Would you like me to create it first?"
                ),
                message="Missing prerequisite: workflow",
            )

        # Generic missing resource
        return CorrectionResult(
            strategy=CorrectionStrategy.CREATE_PREREQUISITE,
            success=False,
            user_question=(
                f"A resource needed by this step is missing: {root_cause}. "
                f"Would you like me to try creating it?"
            ),
            message=f"Missing prerequisite: {root_cause}",
        )

    async def _formulate_user_question(
        self,
        step: dict,
        analysis: FailureAnalysis,
        system_context: Optional[Any],
    ) -> CorrectionResult:
        """
        Generate a specific, actionable question for the user instead
        of just reporting a generic error.
        """
        domain = step.get("domain", "")
        action = step.get("action", "")
        capability_id = f"{domain}.{action}"
        description = step.get("description", "")

        # Build a user-friendly question based on the failure
        category = analysis.category

        if category == FailureCategory.PERMISSION_DENIED:
            question = (
                f"I don't have permission to {description.lower()}. "
                f"This requires a higher access level. "
                f"Please contact your administrator to get the required permissions."
            )
        elif category == FailureCategory.MISSING_RESOURCE:
            question = (
                f"I couldn't complete this step because: {analysis.root_cause}. "
                f"Can you provide the correct resource name or ID, or would you like me to create it?"
            )
        elif category == FailureCategory.WRONG_PARAMETERS:
            if analysis.error_field:
                question = (
                    f"The value for '{analysis.error_field}' was incorrect. "
                    f"Error: {analysis.root_cause}. "
                    f"Can you provide the correct value?"
                )
            else:
                question = (
                    f"Some parameters for '{capability_id}' were incorrect: {analysis.root_cause}. "
                    f"Can you provide more details about what you need?"
                )
        elif category == FailureCategory.EXTERNAL_DEPENDENCY:
            question = (
                f"I couldn't reach the service needed for this step: {analysis.root_cause}. "
                f"Please check that the service is running and try again."
            )
        elif category == FailureCategory.MISSING_CAPABILITY:
            question = (
                f"The platform doesn't currently support '{capability_id}'. "
                f"Would you like me to try an alternative approach?"
            )
        else:
            question = (
                f"Step '{description}' failed: {analysis.root_cause}. "
                f"Would you like me to try a different approach?"
            )

        return CorrectionResult(
            strategy=CorrectionStrategy.ASK_USER,
            success=False,
            user_question=question,
            message=f"Escalated to user: {analysis.category.value}",
        )
