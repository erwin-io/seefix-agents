# SEEFIX Agent Service — Implementation Status

**Project:** SEEFIX — Smart AI Maintenance Prioritization System  
**Service:** `seefix-agents`  
**Runtime:** Python / FastAPI / PostgreSQL / Ollama  
**Model:** `qwen3-vl:2b-instruct-q4_K_M`  
**Status:** Agent backend functional baseline finalized before Reporter Mobile App development  
**Updated:** 2026-10-08

---

## 1. Purpose

`seefix-agents` is the local AI-assisted maintenance operations service for SEEFIX.

It is not the public application API.

Reporter, PPO, Procurement, and maintenance clients communicate with:

```text
seefix-api