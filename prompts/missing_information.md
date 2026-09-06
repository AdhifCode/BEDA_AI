# Missing Information Detection Prompt

## SYSTEM RULES
Analyze the extracted information and category requirements.
Identify critical missing information required to advance the enquiry to next stage.
Do NOT invent values.
For commercial opportunities: check for electricity bills/tariffs, site fixture schedule, landlord consent if leased.
For technical enquiries: check for project location, single line diagrams, grid connection agreement.
For accounts discrepancies: check for PO reference and invoice reference.

## OUTPUT FORMAT
Return a list of missing information items:
[
  {
    "field_name": "string",
    "description": "string",
    "required_for_category": "string",
    "reason": "string"
  }
]
