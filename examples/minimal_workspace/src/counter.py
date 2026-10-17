def count_above(values, threshold):
    """Count integers strictly above threshold."""
    return sum(1 for item in values if item > threshold)
