# Calculator MCP Server

Math evaluation and unit conversion tools exposed via the Model Context Protocol.

## Tools

| Tool | Description |
|------|-------------|
| `calculate` | Safely evaluate math expressions (`+`, `-`, `*`, `/`, `**`, `sqrt`, parentheses) |
| `convert_units` | Convert between common units (km/miles, kg/lbs, celsius/fahrenheit, meters/feet, liters/gallons) |
| `health_check` | Server status check |

## Running

```bash
# Standalone
python -m mcp_servers.calculator.server

# Docker
docker compose up mcp-calculator
```

Runs on port **8004** with SSE transport.

## Examples

```
calculate("sqrt(144) + 2**3")     -> 20
calculate("15 / 100 * 250")       -> 37.5
convert_units(100, "km", "miles") -> 62.1371
convert_units(0, "celsius", "fahrenheit") -> 32
```
