# GEPA Optimization Pre-Flight Checklist

Before running the GEPA optimization, verify all requirements:

## ✅ Installation Checklist

```powershell
# 1. Navigate to project
cd Z:\Aastha\market_data_puller

# 2. Create/activate venv (optional but recommended)
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install dspy-ai openai

# 4. Verify installations
py -c "import dspy; print('DSPy version:', dspy.__version__)"
py -c "import openai; print('OpenAI version:', openai.__version__)"
```

## ✅ Environment Checklist

```powershell
# 1. Set PYTHONPATH (so 'src' module is visible)
$env:PYTHONPATH = (Get-Location)

# 2. Set OpenAI API key
$env:OPENAI_API_KEY = "sk-..."  # Your actual key

# 3. Verify environment
py -c "import os; print('PYTHONPATH:', os.environ.get('PYTHONPATH', 'NOT SET'))"
py -c "import os; print('API Key set:', 'OPENAI_API_KEY' in os.environ)"
```

## ✅ Configuration Verification

Current GEPA settings in `planner_gepa_opt.py`:
- ✅ Model: `gpt-5` (default)
- ✅ Max full evaluations: `300`
- ✅ Num threads: `4` (minimum 2 enforced)
- ✅ Log directory: `scripts/gepa_logs/` (auto-created)
- ✅ Robust JSON parsing: Enabled (handles format errors)
- ✅ Initial prompt: ChainOfThought with field descriptions
- ✅ Training set: 20 diverse queries

## ✅ Run GEPA

```powershell
# Standard run
py .\scripts\planner_gepa_opt.py
```

Expected output:
```
============================================================
GEPA Optimization Configuration:
  Model: gpt-5
  Max Full Evaluations: 300
  Num Threads: 4
  Log Directory: gepa_logs (auto)
============================================================
Loaded 20 training examples
Starting GEPA compilation (this may take a while)...
...
```

## ✅ Verify Outputs

After completion:

```powershell
# 1. Check best prompt was saved
Test-Path .\scripts\gepa_logs\best_prompt.txt

# 2. View the best prompt
Get-Content .\scripts\gepa_logs\best_prompt.txt

# 3. Check log directory
Get-ChildItem .\scripts\gepa_logs\
```

## ⚠️ Common Issues

### Issue: "No module named 'dspy'"
**Solution**: `pip install dspy-ai`

### Issue: "No module named 'src'"
**Solution**: `$env:PYTHONPATH = (Get-Location)`

### Issue: "OpenAI API key not set"
**Solution**: `$env:OPENAI_API_KEY = "sk-..."`

### Issue: "Cannot load Activate.ps1" (execution policy)
**Solution**: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### Issue: Rate limit errors during optimization
**Solution**: 
- Reduce threads: Edit `num_threads=2` in script
- Reduce evals: Edit `max_full_evals=100` in script
- Or wait and retry

### Issue: Model 'gpt-5' not available
**Solution**: Change to available model:
```python
# In planner_gepa_opt.py line 477, change default:
def run_gepa_optimisation(
    *,
    model_name: str = "gpt-4o",  # or "gpt-4o-mini"
    ...
```

## 📊 Monitoring Progress

While GEPA runs (2-6 hours):
- Watch console for evaluation progress
- DSPy will show candidate scores and improvements
- Look for final scores > 0.7 (good) or > 0.8 (excellent)

## 🎯 Success Criteria

GEPA succeeded if:
1. ✅ Script completes without crashing
2. ✅ `best_prompt.txt` file exists in `gepa_logs/`
3. ✅ Final evaluation scores show improvement over training set
4. ✅ Console shows "GEPA compilation complete!"

## 📈 Next Steps After Optimization

1. **Review** the optimized prompt:
   ```powershell
   Get-Content .\scripts\gepa_logs\best_prompt.txt
   ```

2. **Integrate** into orchestrator (manual step):
   - Open `src/agents/orchestrator_agent/planner_stage1.py`
   - Update the prompt/instruction in the PlannerStage1 class
   - Use the best prompt text as guidance

3. **Test** with real queries:
   ```powershell
   py scripts\test_orchestrator.py --query 4
   ```

4. **Measure** quality improvement on your production query set

## 💰 Cost Estimate

For full 300-eval run with `gpt-5`:
- ~6,000 LLM API calls total
- Estimated: **$300-600**

For cheaper testing:
- Use `gpt-4o-mini` (~$30-60 total)
- Or reduce to 50 evals (~$50-100 with gpt-5)

