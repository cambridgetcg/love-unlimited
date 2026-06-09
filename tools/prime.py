#!/usr/bin/env python3
"""
Prime number checking utility.

This module provides functions for checking if a number is prime,
with optimizations for performance.
"""

import math
from typing import Union


def is_prime(n: Union[int, float]) -> bool:
    """
    Check if a number is prime.
    
    A prime number is a natural number greater than 1 that has no positive
    divisors other than 1 and itself.
    
    Args:
        n: The number to check. Can be int or float, but will be converted to int.
        
    Returns:
        True if n is prime, False otherwise.
        
    Raises:
        TypeError: If n cannot be converted to an integer.
        ValueError: If n is less than 2.
        
    Examples:
        >>> is_prime(2)
        True
        >>> is_prime(4)
        False
        >>> is_prime(17)
        True
        >>> is_prime(1)
        False
    """
    # Convert to integer if it's a float
    if isinstance(n, float):
        if not n.is_integer():
            return False
        n = int(n)
    
    # Ensure n is an integer
    try:
        n = int(n)
    except (ValueError, TypeError):
        raise TypeError(f"Cannot convert {n!r} to integer")
    
    # Handle edge cases
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    
    # Check divisibility up to sqrt(n)
    limit = int(math.isqrt(n))
    for i in range(3, limit + 1, 2):
        if n % i == 0:
            return False
    
    return True


def is_prime_optimized(n: int) -> bool:
    """
    Optimized prime checking with additional early checks.
    
    This version includes:
    - Check for small primes (2, 3, 5)
    - Check divisibility by 2 and 3
    - Only check numbers of the form 6k ± 1
    
    Args:
        n: Integer to check for primality.
        
    Returns:
        True if n is prime, False otherwise.
    """
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    
    # Check numbers of the form 6k ± 1 up to sqrt(n)
    limit = int(math.isqrt(n))
    i = 5
    while i <= limit:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    
    return True


def next_prime(n: int) -> int:
    """
    Find the next prime number greater than n.
    
    Args:
        n: Starting number (exclusive).
        
    Returns:
        The smallest prime number greater than n.
        
    Examples:
        >>> next_prime(10)
        11
        >>> next_prime(17)
        19
    """
    if n < 2:
        return 2
    
    candidate = n + 1
    while True:
        if is_prime(candidate):
            return candidate
        candidate += 1


def prev_prime(n: int) -> int:
    """
    Find the previous prime number less than n.
    
    Args:
        n: Starting number (exclusive).
        
    Returns:
        The largest prime number less than n, or None if no such prime exists.
        
    Examples:
        >>> prev_prime(10)
        7
        >>> prev_prime(3)
        2
    """
    if n <= 2:
        return None
    
    candidate = n - 1
    while candidate >= 2:
        if is_prime(candidate):
            return candidate
        candidate -= 1
    
    return None


def prime_factors(n: int) -> list[int]:
    """
    Find the prime factors of a number.
    
    Args:
        n: Integer to factor.
        
    Returns:
        List of prime factors in ascending order.
        
    Examples:
        >>> prime_factors(12)
        [2, 2, 3]
        >>> prime_factors(17)
        [17]
    """
    if n < 2:
        return []
    
    factors = []
    
    # Check for factor 2
    while n % 2 == 0:
        factors.append(2)
        n //= 2
    
    # Check odd factors
    i = 3
    limit = int(math.isqrt(n))
    while i <= limit:
        while n % i == 0:
            factors.append(i)
            n //= i
        i += 2
        limit = int(math.isqrt(n))
    
    # If n is still greater than 1, it's a prime factor
    if n > 1:
        factors.append(n)
    
    return factors


def is_probable_prime(n: int, k: int = 5) -> bool:
    """
    Miller-Rabin primality test (probabilistic).
    
    This is a faster probabilistic test that is correct for all n < 2^64
    when k >= 12. For cryptographic purposes, use a library like
    cryptography or PyCryptodome.
    
    Args:
        n: Integer to test.
        k: Number of iterations (higher = more accurate).
        
    Returns:
        True if n is probably prime, False if n is definitely composite.
    """
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    
    # Write n-1 as 2^r * d
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    
    # Witness loop
    for _ in range(k):
        a = random.randint(2, n - 2)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    
    return True


if __name__ == "__main__":
    import random
    import sys
    
    # Test the functions
    if len(sys.argv) > 1:
        try:
            num = int(sys.argv[1])
            print(f"is_prime({num}) = {is_prime(num)}")
            print(f"is_prime_optimized({num}) = {is_prime_optimized(num)}")
            print(f"prime_factors({num}) = {prime_factors(num)}")
            if num > 0:
                print(f"next_prime({num}) = {next_prime(num)}")
                print(f"prev_prime({num}) = {prev_prime(num)}")
        except ValueError:
            print(f"Error: '{sys.argv[1]}' is not a valid integer")
    else:
        # Run some example tests
        test_numbers = [1, 2, 3, 4, 17, 25, 29, 100, 101]
        print("Testing prime functions:")
        for n in test_numbers:
            print(f"  {n}: prime={is_prime(n)}, factors={prime_factors(n)}")
        
        # Test next_prime and prev_prime
        print("\nTesting next_prime and prev_prime:")
        for n in [10, 20, 30]:
            print(f"  next_prime({n}) = {next_prime(n)}")
            print(f"  prev_prime({n}) = {prev_prime(n)}")