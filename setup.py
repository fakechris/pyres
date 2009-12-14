from distutils.core import setup

setup(name='PyRes',
      version='0.3.0',
      description='Python Resque Job',
      author='Matt George',
      author_email='mgeorge@gmail.com',
      url='http://github.com/binarydud/pyres',
      packages=['pyres', 'resweb'],
      package_data={'resweb': ['templates/*.mustache','media/*']},
      scripts=['scripts/pyres_worker', 'scripts/pyres_web', 'scripts/cleanup_worker']
)
