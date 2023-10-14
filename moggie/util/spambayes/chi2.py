#
# This is code from Spambayes 1.1b3, simplified for use within moggie.
# The original LICENSE.TXT file is included in the same subdirectory.
#
##############################################################################
import math as _math
import random


def chi2Q(x2, v, exp=_math.exp, min=min):
    """Return prob(chisq >= x2, with v degrees of freedom).

    v must be even.
    """
    assert v & 1 == 0
    # XXX If x2 is very large, exp(-m) will underflow to 0.
    m = x2 / 2.0
    sum = term = exp(-m)
    for i in range(1, v//2):
        term *= m / i
        sum += term
    # With small x2 and large v, accumulated roundoff error, plus error in
    # the platform exp(), can cause this to spill a few ULP above 1.0.  For
    # example, chi2Q(100, 300) on my box has sum == 1.0 + 2.0**-52 at this
    # point.  Returning a value even a teensy bit over 1.0 is no good.
    return min(sum, 1.0)


def normZ(z, sqrt2pi=_math.sqrt(2.0*_math.pi), exp=_math.exp):
    "Return value of the unit Gaussian at z."
    return exp(-z*z/2.0) / sqrt2pi


def normP(z):
    """Return area under the unit Gaussian from -inf to z.

    This is the probability that a zscore is <= z.
    """

    # This is very accurate in a fixed-point sense.  For negative z of
    # large magnitude (<= -8.3), it returns 0.0, essentially because
    # P(-z) is, to machine precision, indistiguishable from 1.0 then.

    # sum <- area from 0 to abs(z).
    a = abs(float(z))
    if a >= 8.3:
        sum = 0.5
    else:
        sum2 = term = a * normZ(a)
        z2 = a*a
        sum = 0.0
        i = 1.0
        while sum != sum2:
            sum = sum2
            i += 2.0
            term *= z2 / i
            sum2 += term

    if z >= 0:
        result = 0.5 + sum
    else:
        result = 0.5 - sum

    return result


def normIQ(p, sqrt=_math.sqrt, ln=_math.log):
    """Return z such that the area under the unit Gaussian from z to +inf is p.

    Must have 0.0 <= p <= 1.0.
    """

    assert 0.0 <= p <= 1.0
    # This is a low-accuracy rational approximation from Abramowitz & Stegun.
    # The absolute error is bounded by 3e-3.

    flipped = False
    if p > 0.5:
        flipped = True
        p = 1.0 - p

    if p == 0.0:
        z = 8.3
    else:
        t = sqrt(-2.0 * ln(p))
        z = t - (2.30753 + .27061*t) / (1. + .99229*t + .04481*t**2)

    if flipped:
        z = -z
    return z


def normIP(p):
    """Return z such that the area under the unit Gaussian from -inf to z is p.

    Must have 0.0 <= p <= 1.0.
    """
    z = normIQ(1.0 - p)
    # One Newton step should double the # of good digits.
    return z + (p - normP(z)) / normZ(z)
