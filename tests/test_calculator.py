"""Tests for the Calculator MCP server logic."""

import pytest

from mcp_servers.calculator.server import (
    calculate,
    convert,
    convert_units,
    health_check,
    safe_calculate,
)

# ---------------------------------------------------------------------------
# safe_calculate — core math engine
# ---------------------------------------------------------------------------


class TestSafeCalculate:
    """Tests for the safe_calculate function."""

    def test_addition(self):
        assert safe_calculate("2 + 3") == 5.0

    def test_subtraction(self):
        assert safe_calculate("10 - 4") == 6.0

    def test_multiplication(self):
        assert safe_calculate("6 * 7") == 42.0

    def test_division(self):
        assert safe_calculate("15 / 4") == 3.75

    def test_power(self):
        assert safe_calculate("2 ** 10") == 1024.0

    def test_modulo(self):
        assert safe_calculate("17 % 5") == 2.0

    def test_floor_division(self):
        assert safe_calculate("17 // 5") == 3.0

    def test_parentheses(self):
        assert safe_calculate("(2 + 3) * 4") == 20.0

    def test_nested_parentheses(self):
        assert safe_calculate("((1 + 2) * (3 + 4))") == 21.0

    def test_negative_number(self):
        assert safe_calculate("-5 + 3") == -2.0

    def test_sqrt(self):
        assert safe_calculate("sqrt(144)") == 12.0

    def test_abs_function(self):
        assert safe_calculate("abs(-42)") == 42.0

    def test_complex_expression(self):
        assert safe_calculate("sqrt(144) + 2**3") == 20.0

    def test_percentage(self):
        assert safe_calculate("15 / 100 * 250") == 37.5

    def test_zero_division_raises(self):
        with pytest.raises(ZeroDivisionError):
            safe_calculate("1 / 0")

    def test_unsupported_function_raises(self):
        with pytest.raises(ValueError, match="Unsupported function"):
            safe_calculate("eval('bad')")

    def test_syntax_error_raises(self):
        with pytest.raises(SyntaxError):
            safe_calculate("2 +* 3")


# ---------------------------------------------------------------------------
# convert — unit conversion engine
# ---------------------------------------------------------------------------


class TestConvert:
    """Tests for the convert function."""

    def test_km_to_miles(self):
        assert abs(convert(100, "km", "miles") - 62.1371) < 0.001

    def test_miles_to_km(self):
        assert abs(convert(62.1371, "miles", "km") - 100.0) < 0.01

    def test_kg_to_lbs(self):
        assert abs(convert(1, "kg", "lbs") - 2.20462) < 0.001

    def test_lbs_to_kg(self):
        assert abs(convert(2.20462, "lbs", "kg") - 1.0) < 0.001

    def test_celsius_to_fahrenheit(self):
        assert convert(0, "celsius", "fahrenheit") == 32.0
        assert convert(100, "celsius", "fahrenheit") == 212.0

    def test_fahrenheit_to_celsius(self):
        assert convert(32, "fahrenheit", "celsius") == 0.0
        assert convert(212, "fahrenheit", "celsius") == 100.0

    def test_meters_to_feet(self):
        assert abs(convert(1, "meters", "feet") - 3.28084) < 0.001

    def test_liters_to_gallons(self):
        assert abs(convert(1, "liters", "gallons") - 0.264172) < 0.001

    def test_case_insensitive(self):
        assert abs(convert(1, "KM", "Miles") - 0.621371) < 0.001

    def test_unsupported_conversion_raises(self):
        with pytest.raises(ValueError, match="Unsupported conversion"):
            convert(1, "parsecs", "lightyears")


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------


class TestCalculateTool:
    """Tests for the calculate MCP tool."""

    def test_success(self):
        result = calculate("2 + 3")
        assert result["status"] == "success"
        assert result["result"] == "5"
        assert result["numeric_result"] == 5.0

    def test_decimal_result(self):
        result = calculate("10 / 3")
        assert result["status"] == "success"
        assert float(result["result"]) == pytest.approx(3.33333, rel=1e-3)

    def test_error_returns_error_dict(self):
        result = calculate("1 / 0")
        assert result["status"] == "error"
        assert "error" in result


class TestConvertUnitsTool:
    """Tests for the convert_units MCP tool."""

    def test_success(self):
        result = convert_units(100, "km", "miles")
        assert result["status"] == "success"
        assert float(result["converted_value"]) == pytest.approx(62.1371, rel=1e-3)

    def test_error_returns_error_dict(self):
        result = convert_units(1, "parsecs", "lightyears")
        assert result["status"] == "error"
        assert "error" in result


class TestHealthCheck:
    """Tests for the health_check MCP tool."""

    def test_returns_healthy(self):
        result = health_check()
        assert result["status"] == "healthy"
        assert result["server"] == "calculator"
        assert "timestamp" in result
