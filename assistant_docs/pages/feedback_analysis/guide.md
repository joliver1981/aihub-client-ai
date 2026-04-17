# Feedback Analysis Dashboard

The Feedback Analysis Dashboard provides insights into how users are rating AI agent responses. Use this page to monitor agent performance, identify problematic questions, and improve agent quality over time.

## Overview

The dashboard collects and analyzes feedback that users submit after interacting with AI agents. This feedback helps you:
- Measure agent performance and user satisfaction
- Identify questions that agents struggle with
- Track trends over time
- Prioritize improvements to agents and data dictionaries

## Page Layout

### Header
- **Feedback Analysis Dashboard** title
- **Time Period Selector** - Filter data by time range

### Summary Cards (Top Row)
Four metric cards showing key statistics:

| Card | Color | Description |
|------|-------|-------------|
| Total Feedback | Blue | Total number of feedback submissions |
| Positive Feedback | Green | Count and percentage of positive ratings |
| Negative Feedback | Red | Count and percentage of negative ratings |
| Average Rating | Teal | Mean rating with star visualization |

### Charts Section
Two side-by-side charts:

| Chart | Description |
|-------|-------------|
| **Feedback by Agent** | Bar chart showing feedback distribution across agents |
| **Feedback Trends** | Line chart showing positive/negative trends over time |

### Problematic Questions Table
Lists questions that frequently receive negative feedback:

| Column | Description |
|--------|-------------|
| Question | The user's original question |
| Feedback Count | Number of times this question received feedback |
| Avg Rating | Average rating for this question |
| Avg Confidence | Average AI confidence score |
| Actions | View details button |

### Recent Feedback Table
Detailed list of individual feedback submissions:

| Column | Description |
|--------|-------------|
| Date | When feedback was submitted |
| Agent | Which agent was being used |
| Question | User's original question |
| Type | Positive, Negative, or Detailed |
| Rating | 1-5 star rating |
| Details | User's written feedback (truncated) |
| Confidence | AI's confidence in its answer |
| Caution | Risk level indicator |
| Actions | View full details |

## Time Period Filter

Use the dropdown in the header to filter data:

| Option | Description |
|--------|-------------|
| All Time | All feedback ever collected |
| Last 7 Days | Past week (default) |
| Last 30 Days | Past month |
| Last 90 Days | Past quarter |

Changing the time period updates all metrics, charts, and tables.

## Feedback Types

### Positive Feedback
- Indicated by green badge
- User found the response helpful
- Contributes to positive percentage

### Negative Feedback
- Indicated by red badge
- User found the response unhelpful or incorrect
- Appears in "Problematic Questions" analysis

### Detailed Feedback
- Indicated by blue badge
- User provided written comments
- May include specific suggestions

## Rating System

Ratings are on a 1-5 star scale:
- ⭐ (1) - Very poor
- ⭐⭐ (2) - Poor
- ⭐⭐⭐ (3) - Average
- ⭐⭐⭐⭐ (4) - Good
- ⭐⭐⭐⭐⭐ (5) - Excellent

The average rating is displayed with visual stars in the summary card.

## Confidence Score

The confidence score indicates how certain the AI was about its response:

| Range | Interpretation |
|-------|----------------|
| 90-100% | Very confident |
| 70-89% | Moderately confident |
| 50-69% | Somewhat uncertain |
| Below 50% | Low confidence |

Low confidence responses may indicate:
- Ambiguous questions
- Missing data in the dictionary
- Edge cases the agent wasn't trained for

## Caution Level

Caution levels indicate potential risk in the AI's response:

| Level | Badge | Description |
|-------|-------|-------------|
| Low | Blue | Safe, routine response |
| Medium | Purple | Some uncertainty present |
| High | Yellow | Notable risk factors |
| Very High | Red | Significant caution advised |

## Viewing Feedback Details

Click the eye icon (👁) on any feedback row to open the detail modal showing:
- Full date and time
- Agent name
- Complete question text
- Full AI response (with data tables if applicable)
- Feedback type and rating
- User's written feedback
- Confidence and caution scores

### Data Tables in Responses
If the AI response included a data table:
- Click "Show Data Table" to expand
- Tables are formatted and scrollable
- Download links are styled as buttons

## Filtering Feedback

### Search
Use the search box above the Recent Feedback table to filter by:
- Question text
- Agent name
- Feedback details

### Type Filter
Filter by feedback type:
- All Feedback (default)
- Positive Only
- Negative Only

## Using Feedback Data

### Improving Agents
1. Review problematic questions regularly
2. Check if questions are ambiguous
3. Update data dictionary descriptions
4. Add synonyms for commonly misunderstood terms
5. Create business rules for edge cases

### Identifying Patterns
Look for:
- Specific agents with low ratings
- Time periods with increased negative feedback
- Common question types that fail
- Low confidence + negative feedback combinations

### Training Data
Feedback can be used to:
- Create training examples for future AI improvements
- Document edge cases
- Build FAQ responses for common questions

## Metrics Interpretation

### Good Performance Indicators
- 80%+ positive feedback
- Average rating 4.0+
- Few entries in problematic questions
- Upward trend in feedback charts

### Warning Signs
- Increasing negative feedback percentage
- Same questions appearing repeatedly in problems
- Low confidence on common questions
- Declining average ratings over time

## Best Practices

### Regular Review
- Check dashboard weekly
- Address problematic questions promptly
- Monitor trends after agent updates

### Acting on Feedback
- Prioritize high-volume negative questions
- Update data dictionaries based on user confusion
- Consider adding calculated fields for common requests

### Communication
- Share insights with data owners
- Document improvements made
- Track impact of changes

## Troubleshooting

### No Data Showing
- Check time period filter
- Verify feedback has been submitted
- Ensure database connection is active

### Charts Not Loading
- Refresh the page
- Check browser console for errors
- Verify JavaScript is enabled

### Missing Agent Names
- Agent may have been deleted
- Shows as "Agent #[ID]" instead

## Related Pages

- **Custom Agent Builder** - Update agent configurations
- **Data Dictionary** - Improve column descriptions
- **Assistants** - Where users interact with agents
