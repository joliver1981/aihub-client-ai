# Agent Environments Documentation

## Overview

Agent Environments allow you to create isolated Python environments with custom packages for your AI agents. Each environment runs in its own virtual environment, ensuring complete isolation and reproducibility.

## Quick Start

### Creating Your First Environment

1. Navigate to **Advanced → Agent Environments**
2. Click **Create New Environment**
3. Enter a name and description
4. Select initial packages (optional)
5. Click **Create**

Your environment will be ready in seconds!

### Adding Packages

1. Open your environment in the editor
2. Search for packages or use quick install buttons
3. Optionally specify a version (e.g., `pandas==2.0.0`)
4. Click **Install**

> **Tip:** Start with common packages like `pandas`, `numpy`, and `requests`

## Features

### 🎯 Key Capabilities

- **Isolated Execution**: Each environment is completely isolated
- **Package Management**: Install any approved Python package
- **Version Control**: Pin specific package versions
- **Agent Integration**: Assign environments to specific agents
- **Testing Sandbox**: Test code before deployment

### 📦 Package Management

#### Allowed Packages

Your subscription includes access to these pre-approved packages:

**Data Analysis**
- `pandas` - Data manipulation and analysis
- `numpy` - Numerical computing
- `openpyxl` - Excel file handling
- `xlsxwriter` - Excel file creation

**Web & APIs**
- `requests` - HTTP library
- `beautifulsoup4` - Web scraping
- `lxml` - XML/HTML processing
- `selenium` - Browser automation

**Visualization**
- `matplotlib` - Plotting library
- `seaborn` - Statistical visualization
- `plotly` - Interactive graphs
- `bokeh` - Interactive visualization

**Machine Learning**
- `scikit-learn` - Machine learning
- `tensorflow` - Deep learning
- `torch` - PyTorch deep learning
- `transformers` - NLP models

**Utilities**
- `python-dotenv` - Environment variables
- `pyyaml` - YAML processing
- `jsonschema` - JSON validation
- `jinja2` - Template engine

#### Installing Packages

There are three ways to install packages:

1. **Quick Install**: Click on package badges
2. **Search**: Type package name and click install
3. **With Version**: Specify exact version (e.g., `numpy==1.24.0`)

#### Package Limits

- **Pro Plan**: 50 packages per environment
- **Enterprise**: Unlimited packages

### 🧪 Environment Sandbox

The sandbox allows you to test code in your environments before deploying to agents.

#### Using the Sandbox

1. Go to **Advanced → Environment Sandbox**
2. Select an environment
3. Write or load test code
4. Click **Run** to execute
5. View results in the output console

#### Quick Tests

Pre-built tests are available:

- **Check Packages**: Lists installed packages
- **Test Pandas**: Verify data manipulation
- **Test NumPy**: Check numerical computing
- **Test ML Libraries**: Validate ML packages
- **Agent Template**: Boilerplate for agents

### 🤖 Agent Integration

#### Assigning Environments to Agents

1. Open the environment editor
2. Click **Agent Testing** panel
3. Select an agent from the dropdown
4. Click **Assign Environment**

#### How Agents Use Environments

When an agent has a custom environment:
- The agent executes using that environment's Python
- All installed packages are available
- Complete isolation from other agents

## Best Practices

### Environment Naming

Use descriptive names that indicate purpose:
- ✅ `data-analysis-prod`
- ✅ `ml-experiments-v2`
- ❌ `env1`
- ❌ `test`

### Package Management

1. **Start Small**: Add only needed packages
2. **Pin Versions**: Use specific versions in production
3. **Test First**: Use sandbox before assigning to agents
4. **Document**: Add clear descriptions to environments

### Performance Tips

- Environments are cached for 30 minutes
- First execution may be slower (environment loading)
- Keep package count reasonable (< 30 for best performance)

## Workflows

### Development Workflow

```mermaid
graph LR
    A[Create Environment] --> B[Add Packages]
    B --> C[Test in Sandbox]
    C --> D[Assign to Agent]
    D --> E[Monitor Performance]
    