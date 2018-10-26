from micodymora.Chem import load_chems_dict
from micodymora.Constants import Rkj

import re
import collections
import numpy as np

class ImbalancedReactionException(Exception):
    pass

class Reaction:
    def __init__(self, reagents, name = None):
        '''reagents: {Chem: stoichiometry}'''
        self.reagents = reagents
        self.name = name
        self.dG0 = sum(chem.dGf0 * stoichiometry for chem, stoichiometry in self.reagents.items())

    def K(self, T):
        '''equilibrium constant of the reaction'''
        return np.exp(-self.dG0 / Rkj / T)

    def dG0p(self, T):
        '''Gibbs energy differential of the reaction (kJ.mol-1) assuming that
        every chemical species is at standard concentration, except for H+
        which is at 1e-7 M.'''
        try:
            proton_stoich = next(stoich for chem, stoich in self.reagents.items() if chem.name == "H+")
        except StopIteration:
            proton_stoich = 0
        return self.dG0 + Rkj * T * np.log(1e-7 ** proton_stoich) 

    def check_balance(self, tolerance=0.01):
        '''Checks the elemental balance of the reaction.
        Throws ImbalancedReactionException if reaction is imbalanced.
        Does not do anything remarkable if the reaction is balanced.
        Returns a dictionary containing the imabalance per element (0 if balanced)
        '''
        balance = collections.defaultdict(int)
        for reagent, stoichiometry in self.reagents.items():
            for element, amount in reagent.composition.items():
                balance[element] += stoichiometry * amount
        imbalances = [(element, imbalance) for element, imbalance in balance.items() if imbalance]
        if sum(imbalance for element, imbalance in imbalances) > tolerance:
            imbalance_summary = ", ".join("{}: {}".format(element, imbalance) for element, imbalance in imbalances)
            raise ImbalancedReactionException(imbalance_summary)
        return balance

    @classmethod
    def from_string(cls, chems_dict, reaction_string, name=None):
        '''Creates a Reaction instance from a string.
            The string must comply to the following requirements;
            - reagents are written as specified in the `chems_dict` used
                (see the "name" column in data/chems.csv for example)
            - substrates on the left hand side and products on the right hand side
            - both sides are separated by " --> "
            - reagents are separated by a " + " (spaces are mandatory)
            - stoichiometry of reagents without space (good: "2H2O"; bad: "2 H2O")
            It is possible to instanciate reactions with no product or no
            substrate.
            '''
        reaction_dict = dict()
        substrates_string, products_string = reaction_string.split("-->")
        substrates_string = substrates_string.lstrip().rstrip()
        products_string = products_string.lstrip().rstrip()
        chem_pat = re.compile("^([\d\.]+)?(\S+)")
        if substrates_string:
            individual_substrate_strings = substrates_string.split(" + ")
            for individual_substrate_string in individual_substrate_strings:
                stoichiometry, chem_name = chem_pat.findall(individual_substrate_string)[0]
                chem = chems_dict[chem_name] # raises an error if the chem in the reaction is not recorded in chems.csv
                stoichiometry = stoichiometry and -float(stoichiometry) or -1
                reaction_dict[chem] = stoichiometry
        if products_string:
            individual_product_strings = products_string.split(" + ")
            for individual_product_string in individual_product_strings:
                stoichiometry, chem_name = chem_pat.findall(individual_product_string)[0]
                chem = chems_dict[chem_name] # raises an error if the chem in the reaction is not recorded in chems.csv
                stoichiometry = stoichiometry and float(stoichiometry) or 1
                reaction_dict[chem] = stoichiometry
        return cls(reaction_dict, name=name)

    def new_SimBioReaction(self, chems_list, parameters):
        '''Return a SimBioReaction instance based on the current Reaction
        instance'''
        return SimBioReaction(self.reagents, chems_list, parameters, name=self.name)

    def __mul__(self, factor):
        '''Implement multiplication of reaction's stoichiometry by a numeric factor
        '''
        new_stoichiometry = {reagent: stoich * factor
                             for reagent, stoich
                             in self.reagents.items()}
        return Reaction(new_stoichiometry, self.name)

    def __str__(self):
        formatted_substrates = ["{}{}".format(stoichiometry != -1 and -stoichiometry or "", chem.name)
                                for chem, stoichiometry 
                                in self.reagents.items()
                                if stoichiometry < 0]
        substrates = " + ".join(formatted_substrates)
        formatted_products = ["{}{}".format(stoichiometry != 1 and stoichiometry or "", chem.name)
                                for chem, stoichiometry
                                in self.reagents.items()
                                if stoichiometry > 0]
        products = " + ".join(formatted_products)
        if self.name:
            return "{}: {} --> {}".format(self.name, substrates, products)
        else:
            return "{} --> {}".format(substrates, products)

class SimulationReaction(Reaction):
    '''Represents a chemical reaction occuring during a simulation.
    Instances of this class know the stoichiometric vector corresponding to
    their reaction, and also the rate function of the reaction.
    It is then able to compute its mass action ratios and Gibbs energy when
    given a concentration vector
    '''
    def __init__(self, reagents, chems_list, rate, name = None):
        super().__init__(reagents, name=name)
        self.chems_list = chems_list
        self.rate = rate
        self.stoichiometry_vector = np.zeros(len(chems_list))
        for reagent, stoichiometry in self.reagents.items():
            # if the reaction involves a reagent which is purposefully not
            # included in the simulation (eg: water), ignore it
            if reagent.name in chems_list:
                self.stoichiometry_vector[chems_list.index(reagent.name)] = stoichiometry

    def lnQ(self, C):
        '''Natural logarithm of the mass action ratio of the reaction.
        * C: concentrations vector (mol.L-1)'''
        return sum(stoich * np.log(conc)
                    for stoich, conc
                    in zip(self.stoichiometry_vector, C))

    def dG(self, C, T):
        '''Compute Gibbs energy differential for non-standard conditions of
        temperature and concentrations, in kJ.mol-1.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)'''
        return self.dG0 + Rkj * T * self.lnQ(C)

    def get_vector(self, C, T):
        '''Returns the reaction's stoichiometric vector, containing values in
        molW.molD-1, where D is the substrate by which the reaction'
        stoichiometry is normalized, and W is whatever reagent of the reaction.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)'''
        return self.stoichiometry_vector

    def get_rate(self, C, T):
        '''Returns reaction's rate in molD.molX-1.day-1,
        where D is the substrate by which the reaction's stoichiometry is
        normalized, and X is the biomass catalyzing the reaction.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)'''
        return self.rate(C, T, self)

    @classmethod
    def from_reaction(cls, reaction, chems_list, rate):
        '''Constructor using a Reaction instance as basis'''
        return cls(reaction.reagents, chems_list, rate, name=reaction.name)

    @classmethod
    def from_string(cls, chems_dict, reaction_string, chems_list, rate, name=None):
        '''Constructor from string, overrides Reaction.from_string'''
        reaction = Reaction.from_string(chems_dict, reaction_string, name=name)
        return cls.from_reaction(reaction, chems_list, rate)

# The only difference between SimulationReaction and MetabolicReaction for the
# moment is that MetabolicReaction passes population informations to its rate
# instance
class MetabolicReaction(SimulationReaction):
    '''Represents a chemical reaction attached to a population in a simulation.
    Instances of this class know the stoichiometric vector corresponding to
    their reaction, the rate function of the reaction, and pass the informations
    from the population to which they belong to their rate function.
    It is then able to compute its mass action ratios and Gibbs energy when
    given a concentration vector
    '''
    def __init__(self, reagents, chems_list, rate, name = None):
        super().__init__(reagents, chems_list, rate, name=name)

    def get_rate(self, C, T, population):
        '''Returns reaction's rate in molD.molX-1.day-1,
        where D is the substrate by which the reaction's stoichiometry is
        normalized, and X is the biomass catalyzing the reaction.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)
        * population: population to which the reaction belongs'''
        return self.rate(C, T, self, population)

    @classmethod
    def from_reaction(cls, reaction, chems_list, rate):
        '''Constructor using a Reaction instance as basis'''
        return cls(reaction.reagents, chems_list, rate, name=reaction.name)

    @classmethod
    def from_simulation_reaction(cls):
        '''Constructor using a SimulationReaction instance as basis'''
        return cls(reaction.reagents, reaction.chems_list, reaction.rate, name=reaction.name)

    @classmethod
    def from_string(cls, chems_dict, reaction_string, chems_list, rate, name=None):
        '''Constructor from string, overrides Reaction.from_string'''
        reaction = Reaction.from_string(chems_dict, reaction_string, name=name)
        return cls.from_reaction(reaction, chems_list, rate)

# this class is meant to ultimately replace SimulationReaction and MetabolicReaction
class SimBioReaction(Reaction):
    def __init__(self, reagents, chems_list, parameters, name=None):
        '''
        * reagents: {Chem: stoichiometry} dict
        * chems_list: list of the name of the chemical species (as strings)
        in the same order as in the concentrations vector
        '''
        super().__init__(reagents, name=name)
        self.chems_list = chems_list
        self.stoichiometry_vector = np.zeros(len(self.chems_list))
        self.update_chems_list(chems_list) # the stoichiometry vector is set here
        self.parameters = parameters

    def update_chems_list(self, new_chems_list):
        '''Change the chems list and recompute the stoichiometry vector accordingly'''
        self.chems_list = new_chems_list
        self.stoichiometry_vector = np.zeros(len(self.chems_list))
        for reagent, stoichiometry in self.reagents.items():
            # if the reaction involves a reagent which is purposefully not
            # included in the simulation (eg: water), ignore it
            if reagent.name in self.chems_list:
                self.stoichiometry_vector[self.chems_list.index(reagent.name)] = stoichiometry

    def lnQ(self, C):
        '''Natural logarithm of the mass action ratio of the reaction.
        * C: concentrations vector (mol.L-1)'''
        return sum(stoich * np.log(conc)
                    for stoich, conc
                    in zip(self.stoichiometry_vector, C))

    def disequilibrium(self, C, T):
        '''Mass action ratio divided by equilibrium constant (Q/K)'''
        return np.exp(self.lnQ(C) - np.log(self.K(T)))

    def dG(self, C, T):
        '''Compute Gibbs energy differential for non-standard conditions of
        temperature and concentrations, in kJ.mol-1.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)'''
        return self.dG0 + Rkj * T * self.lnQ(C)

    def get_vector(self, C, T):
        '''Returns the reaction's stoichiometric vector, containing values in
        molW.molD-1, where D is the substrate by which the reaction'
        stoichiometry is normalized, and W is whatever reagent of the reaction.
        * C: concentrations vector (mol.L-1)
        * T: temperature (K)'''
        return self.stoichiometry_vector

    def __mul__(self, factor):
        '''Implement multiplication of reaction's stoichiometry by a numeric factor
        '''
        new_stoichiometry = {reagent: stoich * factor
                             for reagent, stoich
                             in self.reagents.items()}
        return SimBioReaction(new_stoichiometry, self.chems_list, self.parameters, self.name)

def load_reactions_dict(chems_path, reactions_path):
    chems_dict = load_chems_dict(chems_path)
    with open(reactions_path, "r") as reactions_fh:
        reactions_dict = dict()
        for line in reactions_fh:
            reaction_dict = dict()
            reaction_name, reaction_string = line.rstrip().split(": ")
            reaction = Reaction.from_string(chems_dict, reaction_string, name=reaction_name)
            reaction.check_balance()
            reactions_dict[reaction_name] = reaction
    return reactions_dict
