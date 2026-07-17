VALUE NETWORK VENTURES (VNV)
GIS & Carbon Analytics Division

# Solution Architecture Document

dMRV Analytical Dashboard
Logical, Physical, Data, Sequence, and Security Views
Revision History

# Table of Contents

(Right-click the table above and choose "Update Field" in Microsoft Word to populate section page numbers.)

# 1. Introduction

This document describes the solution architecture for the dMRV Analytical Dashboard from five angles: how the system is organised conceptually (logical), how it is deployed onto infrastructure (physical/deployment), how data moves through it (data flow and entity relationships), how key interactions unfold over time (sequence diagrams), and how it is secured. It complements, rather than repeats, the two companion documents: the Software Requirements Specification defines what the system must do, and the Standard Operating Procedure defines how to build and operate it day to day. This document is the reference for why the system is structured the way it is.

## 1.1 Audience

Team Lead / Reviewer - validating the architecture is sound before Stage 2 build work continues.
Developers - using the sequence and deployment views as a build reference.
Any future contributor joining the project after the pilot, who needs the reasoning behind the structure, not just the structure itself.

## 1.2 Related Documents

SRS_dMRV_Dashboard.docx - functional and non-functional requirements, use cases, data dictionary.
SOP_dMRV_Dashboard_Full.docx - step-by-step build and operational procedure, validation rules, incident response.

# 2. Architectural Views Overview

Rather than one diagram trying to show everything, this document follows a views-based approach: each diagram answers one question well. Together they should let a reader reconstruct the whole system without any single diagram becoming unreadable.

# 3. Logical Architecture

The logical view groups functionality by responsibility, without reference to containers, servers, or deployment mechanics. It exists to answer "what does this system conceptually do" independent of "how is it currently deployed" - useful when the deployment approach changes but the underlying responsibilities don't.
Figure 1: Logical Architecture
Three logical layers are distinguished. The Presentation Layer is the dashboard itself - map, KPI, and chart views. The Application/Domain Layer holds the four functional groupings that also appear as the SRS's functional requirement modules: ingestion & validation, analytics & KPI computation, map publishing, and user & access management, all sharing one set of business rules (validation rules, conversion factors, RBAC policy) rather than each maintaining its own copy. The Data Layer separates structured spatial data from unstructured file storage.

# 4. Microservice Architecture

The logical layers above map onto independently deployable microservices, first introduced in the SRS (Section 3) and repeated here for completeness alongside the other views.
Figure 2: Cloud-Native Microservices Architecture
Each box in the logical Application/Domain Layer becomes one or more services here: Ingestion & Validation splits into the Ingestion Service and Validation Service so upload handling and rule-checking can scale independently; Map Publishing wraps GeoServer; Analytics/KPI is its own service since it is the most computationally variable part of the system; and the Dashboard BFF exists purely to shape data for the frontend rather than duplicating business logic already owned by other services.

# 5. Physical / Deployment Architecture

Where the microservice view shows logical service boundaries, this view shows what those services actually run on: a 3-node Kubernetes pool spread across availability zones, with managed data services kept outside the cluster rather than self-hosted.
Figure 3: Physical / Deployment Architecture

## 5.1 Why Managed Data Services

PostgreSQL/PostGIS and object storage are kept as managed services rather than containers inside the cluster. Restarting or rescheduling a stateless service pod is routine; doing the same to a database pod risks data loss without very careful storage configuration. Using a managed service moves that risk to the provider's SLA instead of onto the pilot's own operations.

## 5.2 Node Sizing (Pilot)

For the pilot, three nodes is a starting point, not a hard requirement - Kubernetes will reschedule pods across whatever nodes exist. The grouping above is a guideline for capacity planning as load grows, not a constraint enforced by the platform.

# 6. Data Architecture


## 6.1 Entity Relationship Overview

The core data model, repeated from the SRS data dictionary for architectural context.
Figure 4: Entity Relationship Overview

## 6.2 Data Flow - Level 0 (Context)

The context diagram treats the whole system as one process and shows every external entity it exchanges data with. This is the diagram to hand someone who needs to understand the system's boundary without any internal detail.
Figure 5: Data Flow Diagram - Level 0 (Context)

## 6.3 Data Flow - Level 1 (Detailed)

Level 1 opens up the single context process into its five major processes and three data stores, showing where each flow actually terminates.
Figure 6: Data Flow Diagram - Level 1
Process 5.0 (Manage Analytical Parameters) is deliberately kept separate from Process 2.0 (Compute Analytics/KPI): parameter changes are infrequent and human-driven, while KPI computation is frequent and largely automatic. Keeping them as distinct processes means a parameter change is always an explicit, logged action rather than something that happens implicitly inside a computation run.

# 7. Sequence Diagrams

The following diagrams trace three interactions end to end: getting a dataset in, getting a dashboard view out, and the authentication check that gates every request in between.

## 7.1 Dataset Ingestion & Validation

Figure 7: Sequence - Dataset Ingestion & Validation
Validation is deliberately asynchronous past the Ingestion Service: the GIS Associate gets a batch ID back immediately (202 Accepted) rather than waiting for validation and load to finish, since a batch of several thousand records can take a few minutes (per NFR-03 in the SRS). The Event Bus is what allows the Ingestion Service to hand off and move on to the next upload.

## 7.2 Dashboard KPI / Map Load

Figure 8: Sequence - Dashboard KPI / Map Load
This path is synchronous end to end because the Viewer is waiting on screen for it (per NFR-02, KPI endpoints should respond within 1 second). The Dashboard BFF exists specifically to make this a single round trip from the client's perspective, even though it internally calls the Analytics Service.

## 7.3 Authentication & Authorised Request

Figure 9: Sequence - Authentication & Authorised Request
The token is validated once at the API Gateway (signature and expiry) and role checks happen again at the backend service that owns the resource. This double-check is intentional: the Gateway confirms the caller is who they claim to be, and the individual service still enforces whether that identity is allowed to do the specific thing being asked (RBAC per FR-701 - FR-703 in the SRS).

# 8. Security Architecture

Security is modelled as four nested zones, each more trusted than the one before it, with the two most consequential controls - TLS termination and RBAC enforcement - sitting at the boundary between zones rather than buried inside a single service.
Figure 10: Security Architecture

## 8.1 Threat Considerations


# 9. Scalability and Reliability Considerations


## 9.1 Scaling Independently

Because each service is deployed separately, the ones that see load first as the project count grows - Ingestion and Analytics - can be given more replicas without touching the others. This was the main argument for microservices in the SRS (Section 3.2) and it is what the physical/deployment view (Section 5) is built to support.

## 9.2 Failure Isolation

A crash in the Analytics Service does not take down map publishing or the dashboard's ability to show already-computed KPIs.
The Event Bus decouples Ingestion from Validation, so a temporary Validation Service outage queues work instead of dropping uploads.
The API Gateway is the single most critical component from an availability standpoint; it should run at least two replicas even in the pilot.

## 9.3 Data Durability

The rolling 30-day PostGIS backup (NFR-09) combined with versioned object storage means a bad deployment or bad data load is recoverable within a known window, not an open-ended incident.

# 10. Traceability to SRS and SOP

Cross-references so a reader moving between documents can find the corresponding detail quickly.

# 11. Approval

This architecture is proposed for the pilot sample project and is expected to hold through Stage 3 scaling without structural rework, per the reasoning in Section 9.

**Table 1**

| Field | Detail |
|---|---|
| Document Title | Solution Architecture Document - dMRV Analytical Dashboard |
| Document Owner | Denish M, Junior GIS Associate |
| Reviewed By | Jibotosh, Team Lead |
| Companion Documents | SRS_dMRV_Dashboard.docx, SOP_dMRV_Dashboard_Full.docx |
| Version | 1.0 - Draft for Review |
| Date | 09 July 2026 |
| Classification | Internal - VNV Use Only |


**Table 2**

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0 | 09 July 2026 | Denish M | Initial architecture document: logical, physical/deployment, data, sequence, and security views |


**Table 3**

| View | Question it Answers |
|---|---|
| Logical | What are the conceptual building blocks, independent of how they're deployed? |
| Microservice | How is functionality split into independently deployable services, and how do they communicate? |
| Physical / Deployment | What actually runs where - which node, which managed service, which network zone? |
| Data (ER + DFD) | What data exists, how is it related, and how does it flow through the system? |
| Sequence | In what order do components talk to each other for a specific interaction? |
| Security | What trust boundaries exist, and what enforces them? |


**Table 4**

| Node | Hosts | Sizing Rationale |
|---|---|---|
| Node 1 | API Gateway, Ingestion Service | Ingestion is bursty (large file uploads); isolating it avoids starving other services during a large batch |
| Node 2 | Validation Service, Analytics Service | Both are CPU-bound (geometry checks, regression/statistics), grouped to share compute headroom |
| Node 3 | Map Publishing Service, Dashboard BFF | Both are I/O-bound (serving tiles/API responses) rather than CPU-bound, a different scaling profile from Node 2 |


**Table 5**

| Concern | Mitigation | Reference |
|---|---|---|
| Credential interception | TLS 1.2+ enforced at the API Gateway; no plaintext HTTP endpoint exposed | NFR-04 |
| Token forgery/replay | JWT signature validated on every request; short expiry window | NFR-05, FR-506 |
| Privilege escalation | RBAC re-checked at the owning service, not trusted solely from the gateway | NFR-06, FR-701-703 |
| Credential leakage in code | Secrets injected via secrets manager / environment variables, never committed | SOP Section 11.3 |
| Data tampering without trace | Append-only audit log; soft-delete only, no hard deletes | NFR-19, FR-704 |
| Lateral movement after a service compromise | Private cluster zone has no direct internet route; only the Gateway is internet-facing | Figure 10 |


**Table 6**

| This Document | SRS Reference | SOP Reference |
|---|---|---|
| Section 3 - Logical Architecture | Section 2.2 (Product Functions) | Section 5 (Detailed Development Procedure) |
| Section 4 - Microservice Architecture | Section 3 (System Architecture Overview) | Section 4 (Environment Setup) |
| Section 5 - Physical/Deployment | Section 2.4 (Operating Environment) | Section 4.2 (Local Development Setup) |
| Section 6 - Data Architecture | Section 5 (Data Requirements) | Section 6 (Data Standards) |
| Section 7 - Sequence Diagrams | Section 8 (Use Cases) | Section 5 (Detailed Development Procedure) |
| Section 8 - Security Architecture | Section 7.2 (Security NFRs) | Section 11 (Security and Access Control) |


**Table 7**

| Prepared By | Reviewed By | Date |
|---|---|---|
| Denish M | Jibotosh | 09 July 2026 |
