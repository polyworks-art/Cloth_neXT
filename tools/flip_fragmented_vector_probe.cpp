// Isolated diagnostic of the unmodified bundled FLIP container implementation.
#include "fragmentedvector.h"
#include "markerparticle.h"
#include <iostream>
int main() {
    FragmentedVector<MarkerParticle> points;
    for(int i=0;i<500000;i++) points.push_back(MarkerParticle(vmath::vec3(float(i),0,0)));
    int wrong=0;
    for(int i=0;i<500000;i++) if(points[i].position.x!=float(i)) {
        std::cout<<"index="<<i<<" value="<<points[i].position.x<<" expected="<<i<<"\n";
        wrong++;
    }
    std::cout<<"incorrect_indices="<<wrong<<" count="<<points.size()<<"\n";
    return wrong ? 2 : 0;
}
