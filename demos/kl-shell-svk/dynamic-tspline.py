"""
This demo illustrates the implementation of the formulation developed in 
Josef Kiendl's dissertation

  http://mediatum.ub.tum.de/doc/1002634/464162.pdf

for isogeometric analysis of Kirchhoff--Love thin shells, assuming the
St. Venant--Kirchhoff material model, with analytical integration through
the thickness of the shell.  This example uses T-splines for the 
discretization.  

The basic problem uses the geometry from Figure 4 of the paper

  http://www.public.iastate.edu/~jmchsu/files/Kamensky_et_al-2017-CMAME.pdf

and involves a sphere moving downard with some initial velocity, then being 
repelled by a potential that models contact with a stationary horizontal 
plate.  The plate is included in the mesh for visualization purposes, but 
constrained to have zero displacement in the analysis, and not used to 
compute the contact potential, which is simply hard-coded.  

An archive with the required data file "sphere.iga" can be downloaded from 

  https://www.dropbox.com/s/8es3zj5dsdzjqim/sphere.iga.tgz?dl=1

and extracted using the command "tar -xvzf sphere.iga.tgz".  (Credit to Fei Xu
for designing this geometry in Rhino 3D, using the T-spline plugin.)

Note: Parallel computation with T-spline meshes works, but is not especially
efficient, since the element-by-element extraction data in the Rhino 3D 
input does not contain information about the connectivity of the Bezier 
element mesh, and nothing is currently done in the preprocessing to 
reconstruct this connectivity data.  
"""

from tIGAr import *
from tIGAr.RhinoTSplines import *
from tIGAr.timeIntegration import *

# Check for existence of required data file.
FNAME = "sphere.iga"
import os.path
if(not os.path.isfile(FNAME)):
    if(mpirank==0):
        print("ERROR: The required input file '"+FNAME
              +"' is not present in the working directory. "
              +"Please refer to the docstring at the top of this script.")
    exit()
        

####### Preprocessing #######

if(mpirank==0):
    print("Generating extraction data...")

# Load a control mesh from element-by-element extraction data in the file
# "sphere.iga", which is generated by the T-spline plugin for Rhino 3D.
controlMesh = RhinoTSplineControlMesh(FNAME)

# Assume each component of the shell structure's displacement is discretized
# using the same scalar discrete space used for the components of the
# mapping from parametric to physical space.
splineGenerator = EqualOrderSpline(3,controlMesh)

# Define a region containing the plate, but not the sphere.
# (The plate is at $z=0$, and the sphere is in $z>0$.)
class BdryDomain(SubDomain):
    def inside(self,x,on_boundary):
        return x[2] < DOLFIN_EPS

# Apply a zero BC to all three components of the shell structure displacement
# for degrees of freedom corresponding to control points located in
# the SubDomain BdryDomain.
for i in range(0,3):
    splineGenerator.addZeroDofsByLocation(BdryDomain(),i)

# Write the extraction data.
DIR = "./extraction"
splineGenerator.writeExtraction(DIR)


####### Analysis #######

if(mpirank==0):
    print("Forming extracted spline...")

# Read an extracted spline back in.
QUAD_DEG = 6
spline = ExtractedSpline(splineGenerator,QUAD_DEG)

if(mpirank==0):
    print("Starting analysis...")

# Alternative: For serial computations, or with triangular elements, it is
# possible to load the extraction data from the filesystem (and not need
# to re-generate the extraction every time the program is run).
#spline = ExtractedSpline(DIR,QUAD_DEG)

# The unknown midsurface displacement
y_hom = Function(spline.V) # in homogeneous coordinates
y = spline.rationalize(y_hom) # in physical coordinates

# Quantities from the previous time step
y_old_hom = Function(spline.V)
ydot_old_hom = Function(spline.V)
yddot_old_hom = Function(spline.V)

# Create a time integrator for the displacement.
RHO_INF = Constant(0.5)
DELTA_T = Constant(0.001)
timeInt = GeneralizedAlphaIntegrator(RHO_INF,DELTA_T,y_hom,
                                     (y_old_hom, ydot_old_hom,
                                      yddot_old_hom))

# Get alpha-level quantities for use in the formulation.  (These are linear
# combinations of old and new quantities.  The time integrator assumes that
# they are in homogeneous representation.)
y_alpha = spline.rationalize(timeInt.x_alpha())
ydot_alpha = spline.rationalize(timeInt.xdot_alpha())
yddot_alpha = spline.rationalize(timeInt.xddot_alpha())

# The reference configuration is the mapping from parametric coordinates to
# physical space.
X = spline.F

# The current configuration is defined at the alpha level in the formulation.
x = X + y_alpha

# Helper function to normalize a vector v.
def unit(v):
    return v/sqrt(inner(v,v))

# Helper function to compute geometric quantities for a midsurface
# configuration x.
def shellGeometry(x):

    # Covariant basis vectors:
    dxdxi = spline.parametricGrad(x)
    a0 = as_vector([dxdxi[0,0],dxdxi[1,0],dxdxi[2,0]])
    a1 = as_vector([dxdxi[0,1],dxdxi[1,1],dxdxi[2,1]])
    a2 = unit(cross(a0,a1))

    # Metric tensor:
    a = as_matrix(((inner(a0,a0),inner(a0,a1)),
                   (inner(a1,a0),inner(a1,a1))))
    # Curvature:
    deriva2 = spline.parametricGrad(a2)
    b = -as_matrix(((inner(a0,deriva2[:,0]),inner(a0,deriva2[:,1])),
                    (inner(a1,deriva2[:,0]),inner(a1,deriva2[:,1]))))
    
    return (a0,a1,a2,a,b)

# Use the helper function to obtain shell geometry for the reference
# and current configurations defined earlier.
A0,A1,A2,A,B = shellGeometry(X)
a0,a1,a2,a,b = shellGeometry(x)

# Strain quantities.
epsilon = 0.5*(a - A)
kappa = B - b

# Helper function to convert a 2x2 tensor T to its local Cartesian
# representation, in a shell configuration with metric a, and covariant
# basis vectors a0 and a1.
def cartesian(T,a,a0,a1):
    
    # Raise the indices on the curvilinear basis to obtain contravariant
    # basis vectors a0c and a1c.
    ac = inv(a)
    a0c = ac[0,0]*a0 + ac[0,1]*a1
    a1c = ac[1,0]*a0 + ac[1,1]*a1

    # Perform Gram--Schmidt orthonormalization to obtain the local Cartesian
    # basis vector e0 and e1.
    e0 = unit(a0)
    e1 = unit(a1 - e0*inner(a1,e0))

    # Perform the change of basis on T and return the result.
    ea = as_matrix(((inner(e0,a0c),inner(e0,a1c)),
                    (inner(e1,a0c),inner(e1,a1c))))
    ae = ea.T
    return ea*T*ae

# Use the helper function to compute the strain quantities in local
# Cartesian coordinates.
epsilonBar = cartesian(epsilon,A,A0,A1)
kappaBar = cartesian(kappa,A,A0,A1)

# Helper function to convert a 2x2 tensor to voigt notation, following the
# convention for strains, where there is a factor of 2 applied to the last
# component.  
def voigt(T):
    return as_vector([T[0,0],T[1,1],2.0*T[0,1]])

# The Young's modulus and Poisson ratio:
E = Constant(3e4)
nu = Constant(0.3)

# The material matrix:
D = (E/(1.0 - nu*nu))*as_matrix([[1.0,  nu,   0.0         ],
                                 [nu,   1.0,  0.0         ],
                                 [0.0,  0.0,  0.5*(1.0-nu)]])
# The shell thickness:
h_th = 0.03

# Extension and bending resultants:
nBar = h_th*D*voigt(epsilonBar)
mBar = (h_th**3)*D*voigt(kappaBar)/12.0

# Compute the elastic potential energy density
Wint = 0.5*(inner(voigt(epsilonBar),nBar)
            + inner(voigt(kappaBar),mBar))*spline.dx

# Take the Gateaux derivative of Wint(y_alpha) in the direction of the test
# function z to obtain the internal virtual work.  Because y_alpha is not
# a valid argument to derivative(), we take the derivative w.r.t. y_hom
# instead, then scale by $1/\alpha_f$.
z_hom = TestFunction(spline.V)
z = spline.rationalize(z_hom)
dWint = Constant(1.0/timeInt.ALPHA_F)*derivative(Wint,y_hom,z_hom)


# Note that taking the derivative w.r.t. the homogeneous representation in
# a homogeneous direction is equivalent to the derivative w.r.t. the physical
# representation in the physical direction.  For a sanity check, consider
#
#  f(x) = x^2/2 = (x_{homo}/w)^2/2 .
#
# Then
#  
#  f'(x)z = xz
#
# and
#
#  \frac{d}{dx_{homo}} f(x)z_{homo} = (x_{homo}/w)(1/w)z_{homo} = xz .


# Mass density:
DENS = Constant(10.0)

# Inertial contribution to the residual:
dWmass = DENS*h_th*inner(yddot_alpha,z)*spline.dx

# The penalty potential to model collision with the plate:
PENALTY = Constant(1e8)
gapFunction = conditional(lt(x[2],Constant(0.0)),-x[2],Constant(0.0))
contactForce = as_vector((Constant(0.0),Constant(0.0),PENALTY*gapFunction))
dWext = inner(-contactForce,z)*spline.dx

# The full nonlinear residual for the shell problem:
res = dWmass + dWint + dWext

# Use derivative() to obtain the consistent tangent of the nonlinear residual,
# considered as a function of displacement in homogeneous coordinates.
dRes = derivative(res,y_hom)

# Apply an initial condition to the sphere's velocity.  
timeInt.xdot_old.interpolate(Expression(("0.0","0.0","-10.0"),degree=1))

# Adjust solver settings to be more robust than defaults, due to extreme
# nonlinearities in the problem.
spline.maxIters = 100
spline.relativeTolerance = 1e-3

# Define files in which to accumulate time series for each component of the
# displacement, and the geometry of the control mesh (which is needed for
# visualization in ParaView).
#
# (Using letters x, y, and z instead of numbered components in the file names
# makes loading time series in ParaView more straightforward.)

# For x, y, and z components of displacement:
d0File = File("results/disp-x.pvd")
d1File = File("results/disp-y.pvd")
d2File = File("results/disp-z.pvd")

# For x, y, and z components of initial configuration:
F0File = File("results/F-x.pvd")
F1File = File("results/F-y.pvd")
F2File = File("results/F-z.pvd")

# For weights:
F3File = File("results/F-w.pvd")

for i in range(0,50):

    if(mpirank == 0):
        print("------- Time step "+str(i+1)
              +" , t = "+str(timeInt.t)+" -------")

    # Solve the nonlinear problem for this time step and put the solution
    # (in homogeneous coordinates) in y_hom.
    spline.solveNonlinearVariationalProblem(res,dRes,y_hom)

    # Output fields needed for visualization.
    (d0,d1,d2) = y_hom.split()
    d0.rename("d0","d0")
    d1.rename("d1","d1")
    d2.rename("d2","d2")
    d0File << d0
    d1File << d1
    d2File << d2
    # (Note that the components of spline.F are rational, and cannot be
    # directly outputted to ParaView files.)
    spline.cpFuncs[0].rename("F0","F0")
    spline.cpFuncs[1].rename("F1","F1")
    spline.cpFuncs[2].rename("F2","F2")
    spline.cpFuncs[3].rename("F3","F3")
    F0File << spline.cpFuncs[0]
    F1File << spline.cpFuncs[1]
    F2File << spline.cpFuncs[2]
    F3File << spline.cpFuncs[3]

    # Advance to the next time step.
    timeInt.advance()

    
####### Postprocessing #######

# Notes for plotting the results with ParaView:
#
# Load the time series from all seven files and combine them with the
# Append Attributes filter.  Then use the Calculator filter to define the
# vector field
#
# ((d0+F0)/F3-coordsX)*iHat+((d1+F1)/F3-coordsY)*jHat+((d2+F2)/F3-coordsZ)*kHat
#
# which can then be used in the Warp by Vector filter.  Because the
# parametric domain is artificially stretched out, the result of the Warp by
# Vector filter will be much smaller, and the window will need to be re-sized
# to fit the warped data.  The scale factor on the warp filter may need to
# manually be set to 1.
