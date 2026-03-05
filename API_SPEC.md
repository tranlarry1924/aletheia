# Aletheia: Universal Identity API Specification

This API serves as the **Semantic Gateway** for enterprise identity resolution and governance. It enforces a **Unified Schema** and **Graph-RAG** relationship mapping for all incoming PII.

## Authentication
All requests must include a Bearer Token in the authorization header:
`Authorization: Bearer <your_api_token>`

## Endpoints

### 1. Resolve Identity
`POST /v1/identity/resolve`

Analyzes a proposed action against the Knowledge Graph to prevent duplicate PII creation.

**Request Body:**
```json
{
  "actor_id": "Sales_Bot_01",
  "proposed_action": "Apply 15% discount to Globex renewal",
  "entity_context": {
    "name": "Globex Corp",
    "region": "NA",
    "system_source": "Salesforce_CRM"
  }
}
```

**Responses:**
* **200 OK**: Action approved. No conflicts with the Unified Schema.
* **201 Merged**: Identity resolved. The record was matched to an existing entity in the Graph.
* **403 Flagged**: Action blocked. Violation of **CCPA/GDPR** compliance policy.

---

### 2. Governance Kill Switch (Webhook)
`POST /v1/compliance/purge`

Triggers a synchronized data deletion across all connected silos.

**Request Body:**
```json
{
  "request_id": "REQ-99283",
  "compliance_type": "GDPR_RIGHT_TO_ERASE",
  "canonical_id": "UID-8821-XJ"
}
```

**Response:**
* **202 Accepted**: Purge signal broadcasted to all downstream warehouses.
